import customtkinter as ctk
from tkinter import ttk, messagebox
from datetime import datetime
from database import Database


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
        title.pack(pady=(0, 10))

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

        # Entrada efectivo contado
        ctk.CTkLabel(
            self.frame_caja_abierta,
            text="Efectivo contado por cajero:",
            font=("Arial", 12),
        ).grid(row=2, column=0, padx=10, pady=5, sticky="e")

        self.entry_efectivo_contado = ctk.CTkEntry(
            self.frame_caja_abierta,
            width=140,
            placeholder_text="Ej: 8500.00"
        )
        self.entry_efectivo_contado.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        # Observaciones
        ctk.CTkLabel(
            self.frame_caja_abierta,
            text="Observaciones:",
            font=("Arial", 12),
        ).grid(row=3, column=0, padx=10, pady=5, sticky="ne")

        self.txt_observaciones = ctk.CTkTextbox(
            self.frame_caja_abierta,
            width=280,
            height=60,
            font=("Arial", 11)
        )
        self.txt_observaciones.grid(row=3, column=1, padx=5, pady=5, sticky="w")

        # Botón cerrar caja
        btn_cerrar = ctk.CTkButton(
            self.frame_caja_abierta,
            text="Cerrar caja",
            fg_color="#d9534f",
            width=140,
            command=self.cerrar_caja
        )
        btn_cerrar.grid(row=4, column=0, columnspan=3, padx=5, pady=(8, 10))

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
            WHERE estado = 'abierto'
            ORDER BY fecha_apertura DESC
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
            total_ventas, ef_sis, tar_sis, otros_sis = self._calcular_totales_desde_apertura(fecha_ap)
            texto_caja = (
                f"Caja: {nombre_caja}\n"
                f"Apertura: {fecha_ap}\n"
                f"Abrió: {u_apertura}\n"
                f"Monto inicial: RD$ {float(monto_inicial or 0):.2f}"
            )
            self.lbl_caja_info.configure(text=texto_caja)

            texto_totales = (
                f"TOTAL VENTAS (sistema): RD$ {total_ventas:.2f}\n"
                f"   - Efectivo (sistema): RD$ {ef_sis:.2f}\n"
                f"   - Tarjetas (sistema): RD$ {tar_sis:.2f}\n"
                f"   - Otros (sistema):    RD$ {otros_sis:.2f}"
            )
            self.lbl_totales_info.configure(text=texto_totales)

            # Mostrar frame de caja abierta
            self.frame_caja_abierta.pack(fill="x", padx=5, pady=(0, 5))

    def _calcular_totales_desde_apertura(self, fecha_apertura: str):
        """
        Calcula:
        - total ventas
        - total efectivo sistema
        - total tarjeta sistema
        - total otros sistema
        desde la fecha_apertura hasta ahora, usando pagos_factura.
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Agrupar por tipo_pago para soportar pagos mixtos
        cursor.execute(
            """
            SELECT p.tipo_pago, SUM(p.monto)
            FROM pagos_factura p
            JOIN facturas f ON f.id = p.factura_id
            WHERE f.estado = 'emitida'
              AND datetime(f.fecha) >= datetime(?)
            GROUP BY p.tipo_pago
            """,
            (fecha_apertura,)
        )
        rows = cursor.fetchall()
        conn.close()

        total_ventas = 0.0
        efectivo = 0.0
        tarjeta = 0.0
        otros = 0.0

        for tipo_pago, monto in rows:
            monto = float(monto or 0)
            total_ventas += monto

            t = (tipo_pago or "").lower()
            if t == "efectivo":
                efectivo += monto
            elif "tarjeta" in t:
                tarjeta += monto
            else:
                # incluye transferencias, créditos, etc.
                otros += monto

        return total_ventas, efectivo, tarjeta, otros

    def abrir_caja(self):
        """Abre una nueva caja si no hay otra abierta."""
        if self.caja_abierta is not None:
            messagebox.showwarning(
                "Caja abierta",
                "Ya hay una caja abierta. Debes cerrarla antes de abrir una nueva."
            )
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

        (
            cid, nombre_caja, fecha_ap, fecha_ci,
            u_apertura, u_cierre,
            monto_inicial, total_ventas_guardado,
            total_ef_guardado, total_tar_guardado,
            total_otros_guardado, ef_contado_prev,
            diff_prev, obs_prev, estado
        ) = self.caja_abierta

        # Calcular totales finales (desde apertura hasta ahora)
        total_ventas, ef_sis, tar_sis, otros_sis = self._calcular_totales_desde_apertura(fecha_ap)

        # Lo esperado en efectivo es:
        # efectivo sistema + monto inicial (fondo)
        efectivo_sistema_total = ef_sis + float(monto_inicial or 0)
        diferencia = efectivo_contado - efectivo_sistema_total

        # Tolerancia mínima para considerar 0 (por redondeos)
        tolerance = 0.01

        # Si NO cuadra, primero avisar y no cerrar directamente
        if abs(diferencia) > tolerance:
            if diferencia < 0:
                texto_diff = f"FALTAN RD$ {abs(diferencia):.2f} en caja."
            else:
                texto_diff = f"SOBRAN RD$ {abs(diferencia):.2f} en caja."

            msg = (
                "La caja NO CUADRA.\n\n"
                f"{texto_diff}\n\n"
                f"Efectivo esperado (sistema + fondo): RD$ {efectivo_sistema_total:.2f}\n"
                f"Efectivo contado: RD$ {efectivo_contado:.2f}\n\n"
                "¿Deseas cerrar la caja de todos modos?"
            )
            cerrar_igual = messagebox.askyesno("Caja no cuadrada", msg)

            if not cerrar_igual:
                # Usuario decidió NO cerrar hasta cuadrar manualmente
                return

            # Si decide cerrar con descuadre, forzar observación
            if not observaciones:
                messagebox.showerror(
                    "Observación requerida",
                    "Para cerrar la caja con descuadre debes indicar una observación\n"
                    "(por ejemplo: 'faltó dinero', 'error de conteo', etc.)."
                )
                return
        else:
            # Si cuadra, podemos dejar observaciones vacías (se guarda como NULL)
            if not observaciones:
                observaciones = None
            diferencia = 0.0  # normalizar

        fecha_cierre = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = self.db.get_connection()
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
                self.current_user,
                total_ventas,
                ef_sis,
                tar_sis,
                otros_sis,
                efectivo_contado,
                diferencia,
                observaciones,
                cid
            )
        )

        conn.commit()
        conn.close()

        # Mensaje resumen de cierre
        if abs(diferencia) <= tolerance:
            msg = (
                f"Caja '{nombre_caja}' cerrada y CUADRADA.\n\n"
                f"Total ventas (sistema): RD$ {total_ventas:.2f}\n"
                f"Efectivo esperado (sistema + fondo): RD$ {efectivo_sistema_total:.2f}\n"
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
                f"Efectivo esperado (sistema + fondo): RD$ {efectivo_sistema_total:.2f}\n"
                f"Efectivo contado: RD$ {efectivo_contado:.2f}\n"
                f"Diferencia: RD$ {diferencia:.2f}\n\n"
                f"{texto_diff}\n"
                f"Observación: {observaciones or '(sin observación)'}"
            )

        messagebox.showinfo("Caja cerrada", msg)

        # Limpiar entradas de cierre
        self.entry_efectivo_contado.delete(0, "end")
        self.txt_observaciones.delete("0.0", "end")

        # Recargar estado y historial
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
