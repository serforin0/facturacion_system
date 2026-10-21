import customtkinter as ctk
from tkinter import ttk, messagebox
from datetime import datetime
from database import Database


def calcular_totales_desde_apertura(
    db: Database, fecha_apertura: str, cierre_id: int | None = None
) -> tuple[float, float, float, float]:
    """
    Total ventas y desglose por forma de pago del turno.
    Si hay cierre_id, solo entran facturas de ese turno (y las viejas sin turno, por fecha).
    """
    conn = db.get_connection()
    cursor = conn.cursor()
    if cierre_id:
        cursor.execute(
            """
            SELECT p.tipo_pago, SUM(p.monto)
            FROM pagos_factura p
            JOIN facturas f ON f.id = p.factura_id
            WHERE f.estado = 'emitida'
              AND (
                f.cierre_id = ?
                OR (f.cierre_id IS NULL AND datetime(f.fecha) >= datetime(?))
              )
            GROUP BY p.tipo_pago
            """,
            (cierre_id, fecha_apertura),
        )
    else:
        cursor.execute(
            """
            SELECT p.tipo_pago, SUM(p.monto)
            FROM pagos_factura p
            JOIN facturas f ON f.id = p.factura_id
            WHERE f.estado = 'emitida'
              AND datetime(f.fecha) >= datetime(?)
            GROUP BY p.tipo_pago
            """,
            (fecha_apertura,),
        )
    rows = cursor.fetchall()
    conn.close()

    afecta = {c.lower() for c in db.codigos_afectan_caja()}
    total_ventas = 0.0
    efectivo = 0.0
    tarjeta = 0.0
    otros = 0.0

    for tipo_pago, monto in rows:
        monto = float(monto or 0)
        total_ventas += monto
        t = (tipo_pago or "").lower()
        if t in afecta:
            efectivo += monto
        elif "tarjeta" in t:
            tarjeta += monto
        else:
            otros += monto

    return total_ventas, efectivo, tarjeta, otros


def efectivo_esperado_turno(
    monto_inicial: float,
    efectivo_ventas: float,
    ingresos: float,
    retiros: float,
) -> float:
    """Fondo + ventas en efectivo + ingresos − retiros."""
    return (
        float(monto_inicial or 0)
        + float(efectivo_ventas or 0)
        + float(ingresos or 0)
        - float(retiros or 0)
    )


def abrir_dialogo_movimiento_efectivo(parent, db: Database, usuario, tipo: str, on_saved=None) -> bool:
    """
    Registra un ingreso o un retiro de efectivo en el turno abierto.
    Devuelve True si se guardó.
    """
    tipo = (tipo or "").strip().lower()
    if tipo not in ("ingreso", "retiro"):
        return False
    caja = db.fetch_caja_abierta_row()
    if caja is None:
        messagebox.showwarning(
            "Caja cerrada",
            "Abra el turno en el módulo Caja antes de mover efectivo.",
            parent=parent,
        )
        return False

    titulo = "Ingreso de efectivo" if tipo == "ingreso" else "Retiro de efectivo"
    dlg = ctk.CTkToplevel(parent)
    dlg.title(titulo)
    dlg.geometry("420x240")
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    ctk.CTkLabel(dlg, text=titulo, font=("Arial", 15, "bold")).pack(
        anchor="w", padx=16, pady=(14, 6)
    )
    ctk.CTkLabel(dlg, text="Monto", font=("Arial", 12)).pack(anchor="w", padx=16)
    entry_monto = ctk.CTkEntry(dlg, width=180, placeholder_text="0.00")
    entry_monto.pack(anchor="w", padx=16, pady=(0, 8))
    ctk.CTkLabel(dlg, text="Motivo", font=("Arial", 12)).pack(anchor="w", padx=16)
    entry_motivo = ctk.CTkEntry(dlg, width=360, placeholder_text="Ej. cambio, pago a proveedor")
    entry_motivo.pack(anchor="w", padx=16, pady=(0, 12))

    guardado = {"ok": False}

    def aceptar():
        try:
            monto = float((entry_monto.get() or "").replace(",", "").strip())
        except ValueError:
            messagebox.showerror("Efectivo", "El monto debe ser un número.", parent=dlg)
            return
        if monto <= 0:
            messagebox.showerror("Efectivo", "El monto debe ser mayor que cero.", parent=dlg)
            return
        motivo = (entry_motivo.get() or "").strip()
        if len(motivo) < 2:
            messagebox.showerror("Efectivo", "Indique el motivo del movimiento.", parent=dlg)
            return
        if tipo == "retiro":
            cid, _nombre, fecha_ap = caja[0], caja[1], caja[2]
            monto_inicial = float(caja[6] or 0)
            _tv, ef_sis, _tar, _ot = calcular_totales_desde_apertura(db, fecha_ap, cid)
            ingresos, retiros = db.totales_movimientos_efectivo(cid)
            esperado = efectivo_esperado_turno(monto_inicial, ef_sis, ingresos, retiros)
            if monto > esperado + 0.01:
                messagebox.showerror(
                    "Efectivo",
                    f"El retiro supera el efectivo esperado del turno (RD$ {esperado:.2f}).",
                    parent=dlg,
                )
                return
        db.registrar_movimiento_efectivo(caja[0], tipo, monto, motivo, usuario)
        guardado["ok"] = True
        dlg.destroy()
        if callable(on_saved):
            on_saved()

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(fill="x", padx=16, pady=(4, 12))
    ctk.CTkButton(btns, text="Guardar", fg_color="#15803d", command=aceptar).pack(
        side="left"
    )
    ctk.CTkButton(btns, text="Cancelar", fg_color="#64748b", command=dlg.destroy).pack(
        side="left", padx=8
    )
    entry_monto.focus_set()
    dlg.wait_window()
    return bool(guardado["ok"])


def ejecutar_cierre_caja_desde_fila(
    db: Database,
    caja_abierta: tuple,
    efectivo_contado: float,
    observaciones: str | None,
    *,
    usuario_cierre_display: str | None = None,
    usuario_sesion: str | None = None,
    parent=None,
) -> tuple[bool, str]:
    """
    Cierra el turno de caja (misma lógica que el módulo Caja).
    Retorna (True, mensaje_resumen) si cerró correctamente.
    (False, \"\") si el usuario canceló ante un descuadre.
    (False, mensaje) si hubo error o validación.
    """
    (
        cid,
        nombre_caja,
        fecha_ap,
        _fecha_ci,
        _u_apertura,
        _u_cierre,
        monto_inicial,
        _total_ventas_guardado,
        _total_ef_guardado,
        _total_tar_guardado,
        _total_otros_guardado,
        _ef_contado_prev,
        _diff_prev,
        _obs_prev,
        _estado,
    ) = caja_abierta

    usuario_cierre_db = (
        (usuario_cierre_display or "").strip()
        or (usuario_sesion or "").strip()
        or "—"
    )

    obs = (observaciones or "").strip() or None

    total_ventas, ef_sis, tar_sis, otros_sis = calcular_totales_desde_apertura(
        db, fecha_ap, cid
    )
    ingresos_ef, retiros_ef = db.totales_movimientos_efectivo(cid)
    efectivo_sistema_total = efectivo_esperado_turno(
        monto_inicial, ef_sis, ingresos_ef, retiros_ef
    )
    diferencia = efectivo_contado - efectivo_sistema_total
    tolerance = 0.01

    if abs(diferencia) > tolerance:
        if diferencia < 0:
            texto_diff = f"FALTAN RD$ {abs(diferencia):.2f} en caja."
        else:
            texto_diff = f"SOBRAN RD$ {abs(diferencia):.2f} en caja."

        msg = (
            "La caja NO CUADRA.\n\n"
            f"{texto_diff}\n\n"
            f"Efectivo esperado: RD$ {efectivo_sistema_total:.2f}\n"
            f"  Fondo RD$ {float(monto_inicial or 0):.2f}"
            f" + ventas efectivo RD$ {ef_sis:.2f}"
            f" + ingresos RD$ {ingresos_ef:.2f}"
            f" − retiros RD$ {retiros_ef:.2f}\n"
            f"Efectivo contado: RD$ {efectivo_contado:.2f}\n\n"
            "¿Deseas cerrar la caja de todos modos?"
        )
        if not messagebox.askyesno("Caja no cuadrada", msg, parent=parent):
            return False, ""

        if not obs:
            return (
                False,
                "Para cerrar con descuadre debe indicar una observación "
                "(p. ej. faltó dinero, error de conteo).",
            )
    else:
        if not obs:
            obs = None
        diferencia = 0.0

    fecha_cierre = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE cierres_caja
        SET fecha_cierre = ?,
            usuario_cierre = ?,
            total_ventas = ?,
            total_efectivo_sistema = ?,
            total_tarjeta_sistema = ?,
            total_otros_sistema = ?,
            efectivo_contado = ?,
            diferencia_efectivo = ?,
            observaciones = ?,
            estado = 'cerrado'
        WHERE id = ?
        """,
        (
            fecha_cierre,
            usuario_cierre_db,
            total_ventas,
            ef_sis,
            tar_sis,
            otros_sis,
            efectivo_contado,
            diferencia,
            obs,
            cid,
        ),
    )

    # Otros turnos sin fecha_cierre (duplicados): si no se cierran, la app sigue viendo "caja abierta"
    cursor.execute(
        """
        SELECT id, fecha_apertura, monto_inicial
        FROM cierres_caja
        WHERE fecha_cierre IS NULL
        """
    )
    for oid, fap, mi_orf in cursor.fetchall():
        tv_o, ef_o, tar_o, ot_o = calcular_totales_desde_apertura(db, fap, oid)
        ing_o, ret_o = db.totales_movimientos_efectivo(oid)
        mi_f = float(mi_orf or 0)
        ef_esp_o = efectivo_esperado_turno(mi_f, ef_o, ing_o, ret_o)
        obs_o = (
            "Cierre automático: había otro turno sin cerrar en la base de datos "
            "(duplicado). Revise el historial."
        )
        cursor.execute(
            """
            UPDATE cierres_caja
            SET fecha_cierre = ?,
                usuario_cierre = ?,
                total_ventas = ?,
                total_efectivo_sistema = ?,
                total_tarjeta_sistema = ?,
                total_otros_sistema = ?,
                efectivo_contado = ?,
                diferencia_efectivo = ?,
                observaciones = ?,
                estado = 'cerrado'
            WHERE id = ?
            """,
            (
                fecha_cierre,
                usuario_cierre_db,
                tv_o,
                ef_o,
                tar_o,
                ot_o,
                ef_esp_o,
                0.0,
                obs_o,
                oid,
            ),
        )

    conn.commit()
    conn.close()

    if abs(diferencia) <= tolerance:
        msg = (
            f"Caja '{nombre_caja}' cerrada y CUADRADA.\n\n"
            f"Total ventas (sistema): RD$ {total_ventas:.2f}\n"
            f"Efectivo esperado: RD$ {efectivo_sistema_total:.2f}\n"
            f"  Fondo + ventas efectivo + ingresos − retiros\n"
            f"Efectivo contado: RD$ {efectivo_contado:.2f}\n"
            f"Diferencia: RD$ 0.00"
        )
    else:
        if diferencia < 0:
            texto_diff = f"FALTARON RD$ {abs(diferencia):.2f}."
        else:
            texto_diff = f"SOBRARON RD$ {abs(diferencia):.2f}."
        msg = (
            f"Caja '{nombre_caja}' cerrada con DESCUADRE.\n\n"
            f"Total ventas (sistema): RD$ {total_ventas:.2f}\n"
            f"Efectivo esperado: RD$ {efectivo_sistema_total:.2f}\n"
            f"Efectivo contado: RD$ {efectivo_contado:.2f}\n"
            f"Diferencia: RD$ {diferencia:.2f}\n\n"
            f"{texto_diff}\n"
            f"Observación: {obs or '(sin observación)'}"
        )

    return True, msg


class CajaManager:
    def __init__(self, parent, current_user=None, on_caja_abierta=None):
        self.parent = parent
        self.current_user = current_user
        self.db = Database()

        # Callback para ir a facturación cuando se abre la caja
        self.on_caja_abierta = on_caja_abierta

        # Estado actual de caja (fila de la tabla cierres_caja)
        self.caja_abierta = None

        # Widgets importantes
        self.main_frame = None
        self.estado_label = None

        self.frame_apertura = None
        self.entry_nombre_caja = None
        self.entry_monto_inicial = None

        self.frame_caja_abierta = None
        self.lbl_caja_info = None
        self.lbl_totales_info = None
        self.entry_efectivo_contado = None
        self.txt_observaciones = None

        self.tree_historial = None

        self._setup_ui()
        self._load_estado_caja()
        self._load_historial()

    # ====================================================
    #                      UI
    # ====================================================

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Título
        title = ctk.CTkLabel(
            self.main_frame,
            text="💰 MÓDULO DE CAJA (ARQUEO / CIERRE)",
            font=("Arial", 20, "bold"),
            text_color="white"
        )
        title.pack(pady=(0, 6))
        extra = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        extra.pack(fill="x", padx=5, pady=(0, 8))
        ctk.CTkButton(
            extra,
            text="Métodos de pago",
            width=150,
            fg_color="#334155",
            command=self._abrir_metodos,
        ).pack(side="left", padx=4)

        # ------------------------------
        #   ESTADO ACTUAL + ACCIONES
        # ------------------------------
        top_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        top_frame.pack(fill="x", padx=5, pady=(0, 10))

        self.estado_label = ctk.CTkLabel(
            top_frame,
            text="Estado de caja: desconocido",
            font=("Arial", 14, "bold"),
            text_color="white"
        )
        self.estado_label.pack(anchor="w", padx=10, pady=8)

        # Frame para cuando NO hay caja abierta (apertura)
        self.frame_apertura = ctk.CTkFrame(top_frame, fg_color="#1F1F1F")
        self.frame_apertura.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(
            self.frame_apertura,
            text="Apertura de caja",
            font=("Arial", 14, "bold"),
            text_color="white"
        ).grid(row=0, column=0, columnspan=4, padx=10, pady=(5, 10), sticky="w")

        ctk.CTkLabel(
            self.frame_apertura,
            text="Nombre de caja:",
            font=("Arial", 12),
        ).grid(row=1, column=0, padx=10, pady=5, sticky="e")

        self.entry_nombre_caja = ctk.CTkEntry(
            self.frame_apertura,
            width=160,
            placeholder_text="Ej: Caja 1"
        )
        self.entry_nombre_caja.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.entry_nombre_caja.insert(0, "Caja 1")

        ctk.CTkLabel(
            self.frame_apertura,
            text="Monto inicial (efectivo):",
            font=("Arial", 12),
        ).grid(row=1, column=2, padx=10, pady=5, sticky="e")

        self.entry_monto_inicial = ctk.CTkEntry(
            self.frame_apertura,
            width=120,
            placeholder_text="Ej: 1000.00"
        )
        self.entry_monto_inicial.grid(row=1, column=3, padx=5, pady=5, sticky="w")

        btn_abrir = ctk.CTkButton(
            self.frame_apertura,
            text="Abrir caja",
            fg_color="#2fa572",
            width=120,
            command=self.abrir_caja
        )
        btn_abrir.grid(row=2, column=0, columnspan=4, padx=5, pady=(8, 10))

        self.frame_apertura.grid_columnconfigure(1, weight=1)

        # Frame para cuando HAY caja abierta (información + cierre)
        self.frame_caja_abierta = ctk.CTkFrame(top_frame, fg_color="#1F1F1F")
        # Se empacará dinámicamente cuando haya caja abierta

        # Info caja
        self.lbl_caja_info = ctk.CTkLabel(
            self.frame_caja_abierta,
            text="",
            font=("Arial", 12),
            text_color="white",
            justify="left"
        )
        self.lbl_caja_info.grid(row=0, column=0, columnspan=3, padx=10, pady=(5, 5), sticky="w")

        # Info totales esperados
        self.lbl_totales_info = ctk.CTkLabel(
            self.frame_caja_abierta,
            text="",
            font=("Arial", 12),
            text_color="lightgreen",
            justify="left"
        )
        self.lbl_totales_info.grid(row=1, column=0, columnspan=3, padx=10, pady=(0, 5), sticky="w")

        mov_btns = ctk.CTkFrame(self.frame_caja_abierta, fg_color="transparent")
        mov_btns.grid(row=2, column=0, columnspan=3, padx=10, pady=(0, 4), sticky="w")
        ctk.CTkButton(
            mov_btns,
            text="Ingreso de efectivo",
            width=160,
            fg_color="#15803d",
            command=lambda: self._dialogo_movimiento("ingreso"),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            mov_btns,
            text="Retiro de efectivo",
            width=160,
            fg_color="#b45309",
            command=lambda: self._dialogo_movimiento("retiro"),
        ).pack(side="left")

        self.lbl_movimientos = ctk.CTkLabel(
            self.frame_caja_abierta,
            text="",
            font=("Arial", 11),
            text_color="#e2e8f0",
            justify="left",
        )
        self.lbl_movimientos.grid(row=3, column=0, columnspan=3, padx=10, pady=(0, 4), sticky="w")

        # Entrada efectivo contado
        ctk.CTkLabel(
            self.frame_caja_abierta,
            text="Efectivo contado por cajero:",
            font=("Arial", 12),
        ).grid(row=4, column=0, padx=10, pady=5, sticky="e")

        self.entry_efectivo_contado = ctk.CTkEntry(
            self.frame_caja_abierta,
            width=140,
            placeholder_text="Ej: 8500.00"
        )
        self.entry_efectivo_contado.grid(row=4, column=1, padx=5, pady=5, sticky="w")

        # Observaciones
        ctk.CTkLabel(
            self.frame_caja_abierta,
            text="Observaciones:",
            font=("Arial", 12),
        ).grid(row=5, column=0, padx=10, pady=5, sticky="ne")

        self.txt_observaciones = ctk.CTkTextbox(
            self.frame_caja_abierta,
            width=280,
            height=60,
            font=("Arial", 11)
        )
        self.txt_observaciones.grid(row=5, column=1, padx=5, pady=5, sticky="w")

        # Botón cerrar caja
        btn_cerrar = ctk.CTkButton(
            self.frame_caja_abierta,
            text="Cerrar caja",
            fg_color="#d9534f",
            width=140,
            command=self.cerrar_caja
        )
        btn_cerrar.grid(row=6, column=0, columnspan=3, padx=5, pady=(8, 10))

        self.frame_caja_abierta.grid_columnconfigure(1, weight=1)

        # ------------------------------
        #   HISTORIAL DE CIERRES
        # ------------------------------
        historial_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        historial_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        lbl_hist = ctk.CTkLabel(
            historial_frame,
            text="Historial de cierres de caja",
            font=("Arial", 15, "bold"),
            text_color="white"
        )
        lbl_hist.pack(anchor="w", padx=5, pady=(0, 5))

        tree_container = ctk.CTkFrame(historial_frame, fg_color="#2B2B2B")
        tree_container.pack(fill="both", expand=True)

        cols = (
            "id", "caja", "apertura", "cierre",
            "u_apertura", "u_cierre",
            "efectivo_sistema", "efectivo_contado",
            "diferencia", "estado"
        )

        self.tree_historial = ttk.Treeview(
            tree_container,
            columns=cols,
            show="headings",
            height=7
        )

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=22,
            fieldbackground="#2a2d2e",
            borderwidth=0
        )
        style.configure(
            "Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 11, "bold")
        )
        style.map('Treeview', background=[('selected', '#22559b')])

        headers = [
            ("ID", 50),
            ("Caja", 80),
            ("Apertura", 140),
            ("Cierre", 140),
            ("Abrió", 90),
            ("Cerró", 90),
            ("Ef. sistema", 100),
            ("Ef. contado", 100),
            ("Dif.", 80),
            ("Estado", 80),
        ]

        for (col, (text, width)) in zip(cols, headers):
            self.tree_historial.heading(col, text=text)
            self.tree_historial.column(col, width=width, anchor="center")

        # Tag para marcar cierres con descuadre (en rojo)
        self.tree_historial.tag_configure("descuadre", foreground="red")

        scroll_y = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree_historial.yview)
        self.tree_historial.configure(yscrollcommand=scroll_y.set)

        self.tree_historial.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

    # ====================================================
    #                  LÓGICA DE CAJA
    # ====================================================

    def _load_estado_caja(self):
        """Carga si hay una caja abierta y actualiza la UI."""
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, nombre_caja, fecha_apertura, fecha_cierre,
                   usuario_apertura, usuario_cierre,
                   monto_inicial, total_ventas,
                   total_efectivo_sistema, total_tarjeta_sistema,
                   total_otros_sistema, efectivo_contado,
                   diferencia_efectivo, observaciones, estado
            FROM cierres_caja
            WHERE fecha_cierre IS NULL
            ORDER BY datetime(fecha_apertura) DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        conn.close()

        self.caja_abierta = row

        # Limpiar ambos frames
        self.frame_apertura.pack_forget()
        self.frame_caja_abierta.pack_forget()

        if self.caja_abierta is None:
            # No hay caja abierta
            self.estado_label.configure(text="Estado de caja: ❌ NO HAY CAJA ABIERTA")
            self.frame_apertura.pack(fill="x", padx=5, pady=(0, 5))
        else:
            # Hay una caja abierta
            (
                cid, nombre_caja, fecha_ap, fecha_ci,
                u_apertura, u_cierre,
                monto_inicial, total_ventas,
                total_ef_sis, total_tar_sis,
                total_otros_sis, ef_contado,
                diff_ef, obs, estado
            ) = self.caja_abierta

            self.estado_label.configure(
                text=f"Estado de caja: ✅ ABIERTA ({nombre_caja})"
            )

            # Actualizar totales en tiempo real (según ventas desde apertura hasta ahora)
            total_ventas, ef_sis, tar_sis, otros_sis = calcular_totales_desde_apertura(
                self.db, fecha_ap, cid
            )
            desglose = self.db.desglose_pagos_turno(fecha_ap, cid)
            texto_caja = (
                f"Caja: {nombre_caja}\n"
                f"Apertura: {fecha_ap}\n"
                f"Abrió: {u_apertura}\n"
                f"Monto inicial: RD$ {float(monto_inicial or 0):.2f}"
            )
            self.lbl_caja_info.configure(text=texto_caja)

            ingresos_ef, retiros_ef = self.db.totales_movimientos_efectivo(cid)
            esperado_ef = efectivo_esperado_turno(
                monto_inicial, ef_sis, ingresos_ef, retiros_ef
            )
            texto_totales = (
                f"TOTAL VENTAS (sistema): RD$ {total_ventas:.2f}\n"
                f"   - Efectivo (sistema): RD$ {ef_sis:.2f}\n"
                f"   - Tarjetas (sistema): RD$ {tar_sis:.2f}\n"
                f"   - Otros (sistema):    RD$ {otros_sis:.2f}\n"
                f"Ingresos de efectivo: RD$ {ingresos_ef:.2f}\n"
                f"Retiros de efectivo:  RD$ {retiros_ef:.2f}\n"
                f"EFECTIVO ESPERADO: RD$ {esperado_ef:.2f}\n"
                + (
                    "Por método: "
                    + ", ".join(f"{t} RD$ {float(m):.2f}" for t, m in desglose)
                    if desglose
                    else "Por método: sin cobros en este turno"
                )
            )
            self.lbl_totales_info.configure(text=texto_totales)
            movs = self.db.listar_movimientos_efectivo(cid)
            if not movs:
                self.lbl_movimientos.configure(text="Sin ingresos ni retiros en este turno.")
            else:
                lineas = []
                for _mid, tipo, monto, motivo, _usu, fecha in movs[-6:]:
                    signo = "+" if (tipo or "") == "ingreso" else "−"
                    hora = str(fecha or "")[11:16]
                    lineas.append(f"{signo} RD$ {float(monto):.2f}  {motivo}  {hora}")
                extra = ""
                if len(movs) > 6:
                    extra = f"\n… y {len(movs) - 6} movimiento(s) anterior(es)"
                self.lbl_movimientos.configure(text="\n".join(lineas) + extra)

            # Sugerir el efectivo esperado para cuadrar sin descuadre
            self.entry_efectivo_contado.delete(0, "end")
            self.entry_efectivo_contado.insert(0, f"{esperado_ef:.2f}")

            # Mostrar frame de caja abierta
            self.frame_caja_abierta.pack(fill="x", padx=5, pady=(0, 5))

    def _calcular_totales_desde_apertura(self, fecha_apertura: str):
        return calcular_totales_desde_apertura(self.db, fecha_apertura)

    def _abrir_metodos(self):
        from operaciones_pos import MetodosPagoFrame

        top = ctk.CTkToplevel(self.parent.winfo_toplevel())
        top.title("Métodos de pago")
        top.geometry("640x420")
        MetodosPagoFrame(top).pack(fill="both", expand=True)

    def _dialogo_movimiento(self, tipo: str):
        abrir_dialogo_movimiento_efectivo(
            self.parent.winfo_toplevel(),
            self.db,
            self.current_user,
            tipo,
            on_saved=self._load_estado_caja,
        )

    def abrir_caja(self):
        """Abre una nueva caja si no hay otra abierta."""
        if self.caja_abierta is not None:
            messagebox.showwarning(
                "Caja abierta",
                "Ya hay una caja abierta. Debes cerrarla antes de abrir una nueva."
            )
            return

        conn_chk = self.db.get_connection()
        cur_chk = conn_chk.cursor()
        cur_chk.execute(
            "SELECT id FROM cierres_caja WHERE fecha_cierre IS NULL LIMIT 1"
        )
        dup = cur_chk.fetchone()
        conn_chk.close()
        if dup is not None:
            messagebox.showwarning(
                "Caja abierta",
                "Ya existe un turno sin cerrar en la base de datos. "
                "Cierre la caja en este módulo antes de abrir otra.",
            )
            self._load_estado_caja()
            return

        nombre_caja = self.entry_nombre_caja.get().strip() or "Caja 1"
        monto_texto = self.entry_monto_inicial.get().strip() or "0"

        try:
            monto_inicial = float(monto_texto)
        except ValueError:
            messagebox.showerror("Error", "Monto inicial inválido.")
            return

        fecha_ap = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO cierres_caja
                (nombre_caja, fecha_apertura, usuario_apertura,
                 monto_inicial, total_ventas,
                 total_efectivo_sistema, total_tarjeta_sistema, total_otros_sistema,
                 efectivo_contado, diferencia_efectivo,
                 observaciones, estado)
            VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0, NULL, 'abierto')
            """,
            (
                nombre_caja,
                fecha_ap,
                self.current_user,
                monto_inicial
            )
        )

        conn.commit()
        conn.close()

        messagebox.showinfo(
            "Caja abierta",
            f"Caja '{nombre_caja}' abierta correctamente con RD$ {monto_inicial:.2f}."
        )

        self._load_estado_caja()
        self._load_historial()

        # 👉 Ir automáticamente al módulo de facturación si se definió callback
        if callable(self.on_caja_abierta):
            self.on_caja_abierta()

    def cerrar_caja(self):
        """
        Cierra la caja abierta, calcula diferencia y actualiza registro.
        - Si la caja NO cuadra, NO se cierra hasta que el usuario confirme.
        - Si decide cerrar con descuadre, es obligatorio poner una observación.
        """
        if self.caja_abierta is None:
            messagebox.showwarning("Sin caja", "No hay caja abierta para cerrar.")
            return

        ef_texto = self.entry_efectivo_contado.get().strip()
        if not ef_texto:
            messagebox.showerror("Error", "Debes indicar el efectivo contado.")
            return

        try:
            efectivo_contado = float(ef_texto)
        except ValueError:
            messagebox.showerror("Error", "Efectivo contado inválido.")
            return

        observaciones = self.txt_observaciones.get("0.0", "end").strip()

        parent = self.parent.winfo_toplevel() if self.parent else None
        ok, msg = ejecutar_cierre_caja_desde_fila(
            self.db,
            self.caja_abierta,
            efectivo_contado,
            observaciones,
            usuario_cierre_display=None,
            usuario_sesion=self.current_user,
            parent=parent,
        )
        if not ok:
            if msg:
                messagebox.showerror("Cierre de caja", msg, parent=parent)
            return

        messagebox.showinfo("Caja cerrada", msg, parent=parent)

        self.entry_efectivo_contado.delete(0, "end")
        self.txt_observaciones.delete("0.0", "end")

        self._load_estado_caja()
        self._load_historial()

    # ====================================================
    #                HISTORIAL DE CIERRES
    # ====================================================

    def _load_historial(self):
        """Carga los últimos cierres de caja en el Treeview."""
        for item in self.tree_historial.get_children():
            self.tree_historial.delete(item)

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, nombre_caja, fecha_apertura, fecha_cierre,
                   usuario_apertura, usuario_cierre,
                   total_efectivo_sistema, efectivo_contado,
                   diferencia_efectivo, estado
            FROM cierres_caja
            ORDER BY fecha_apertura DESC
            LIMIT 100
            """
        )

        rows = cursor.fetchall()
        conn.close()

        tolerance = 0.01

        for row in rows:
            (
                cid, nombre_caja, fecha_ap, fecha_ci,
                u_apertura, u_cierre,
                ef_sis, ef_contado, diff, estado
            ) = row

            ef_sis = float(ef_sis or 0)
            ef_contado = float(ef_contado or 0)
            diff = float(diff or 0)

            # Tag visual: rojo si hay descuadre
            tags = ()
            if abs(diff) > tolerance:
                tags = ("descuadre",)

            self.tree_historial.insert(
                "",
                "end",
                values=(
                    cid,
                    nombre_caja,
                    fecha_ap or "",
                    fecha_ci or "",
                    u_apertura or "",
                    u_cierre or "",
                    f"RD$ {ef_sis:.2f}",
                    f"RD$ {ef_contado:.2f}",
                    f"RD$ {diff:.2f}",
                    estado
                ),
                tags=tags
            )
