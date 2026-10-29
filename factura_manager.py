import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

import customtkinter as ctk
from PIL import Image
from tkinter import BooleanVar, messagebox, ttk

from caja_manager import abrir_dialogo_movimiento_efectivo
from config_impresion_dialog import open_config_impresion
from database import Database
from operacion_local import (
    asiento_venta,
    autorizacion_ok,
    limpiar_nota,
    partir_lineas,
    precio_con_extras,
    propina_legal,
    puntos_ganados,
    rebaja_puntos,
)
from pos_catalogo_dialog import PosCatalogoDialog

# ITBIS ventas RD (precio en catálogo = base sin ITBIS; el impuesto se suma en factura si aplica)
TASA_ITBIS = 0.18


def normalizar_codigo_conduce(codigo: str) -> str:
    """C42 y c0042 son el mismo pedido: C0042."""
    limpio = "".join(ch for ch in (codigo or "").upper() if ch.isalnum())
    if limpio.startswith("C") and limpio[1:].isdigit():
        return "C" + str(int(limpio[1:])).zfill(4)
    return limpio


def linea_ticket_desde_conduce(linea: dict, componentes: list | None = None) -> dict | None:
    """Pasa una línea guardada por el mesero al formato del ticket de escritorio.

    El nombre y el precio ya incluyen extras. El total guardado es lo que se cobra,
    con el ITBIS dentro, así que no se vuelve a calcular la promoción.
    """
    cantidad = float(linea.get("cantidad") or 0)
    if cantidad <= 0:
        return None
    precio = round(float(linea.get("precio") or 0), 2)
    aplica = linea.get("itbis") is not False
    nombre = str(linea.get("nombre") or "Producto").strip() or "Producto"
    subtotal_bruto = round(precio * cantidad, 2)
    total_guardado = linea.get("total")
    if total_guardado is None:
        subtotal_neto = subtotal_bruto
        impuesto = round(subtotal_neto * TASA_ITBIS, 2) if aplica else 0.0
        total_linea = round(subtotal_neto + impuesto, 2)
        descuento = 0.0
    else:
        total_linea = round(float(total_guardado), 2)
        if aplica and total_linea > 0:
            subtotal_neto = round(total_linea / (1 + TASA_ITBIS), 2)
            impuesto = round(total_linea - subtotal_neto, 2)
        else:
            subtotal_neto = total_linea
            impuesto = 0.0
        if subtotal_bruto + 0.009 < subtotal_neto:
            subtotal_bruto = subtotal_neto
            precio = round(subtotal_neto / cantidad, 2) if cantidad else precio
            descuento = 0.0
        else:
            descuento = round(subtotal_bruto - subtotal_neto, 2)
            if descuento < 0.009:
                descuento = 0.0
                subtotal_neto = subtotal_bruto
                impuesto = round(subtotal_neto * TASA_ITBIS, 2) if aplica else 0.0
                total_linea = round(subtotal_neto + impuesto, 2)
    item = {
        "id": None if linea.get("combo") else linea.get("id"),
        "nombre": nombre,
        "cantidad": cantidad,
        "precio": precio,
        "descuento": descuento,
        "subtotal_bruto": subtotal_bruto,
        "subtotal_neto": subtotal_neto,
        "aplica_itbis": aplica,
        "impuesto_item": impuesto,
        "total_linea": total_linea,
    }
    if linea.get("combo"):
        item["id"] = None
        item["combo_id"] = linea.get("id")
        item["componentes"] = [
            {"id": int(c["id"]), "cantidad": float(c["cantidad"])}
            for c in (componentes or [])
        ]
    return item


def marcar_conduce_facturado(cursor, conduce_id: int, factura_id: int) -> None:
    """Cierra el pedido y suelta la mesa. Va dentro de la misma transacción de la venta."""
    cursor.execute(
        "SELECT estado, mesa FROM conduces WHERE id=?",
        (int(conduce_id),),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError("Ese pedido del mesero ya no está.")
    if row[0] != "pendiente":
        raise ValueError("Ese pedido del mesero ya se facturó.")
    cursor.execute(
        """
        UPDATE conduces
        SET estado='facturado', factura_id=?
        WHERE id=? AND estado='pendiente'
        """,
        (int(factura_id), int(conduce_id)),
    )
    if cursor.rowcount != 1:
        raise ValueError("Ese pedido del mesero ya se facturó.")
    cursor.execute(
        "DELETE FROM mesa_ocupada WHERE mesa=? OR conduce_id=?",
        (row[1], int(conduce_id)),
    )


def _parse_monto_pago(txt: str) -> float:
    """
    Convierte texto de monto a float. Acepta coma de miles (53,141.30) y coma decimal (53141,30),
    alineado con el diálogo de pago en facturacion_erp_manager.
    """
    s = (txt or "").strip().replace(" ", "")
    if not s:
        return 0.0
    for sym in ("RD$", "US$", "$"):
        if s.upper().startswith(sym.upper()):
            s = s[len(sym) :].strip()
            break
    last_c = s.rfind(",")
    last_d = s.rfind(".")
    if last_c > last_d and last_c != -1:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    return float(s)


class FacturaManager:
    def __init__(
        self,
        parent,
        current_user=None,
        duplicar_desde_factura_id: int | None = None,
    ):
        self.parent = parent
        self.db = Database()
        self.current_user = current_user  # usuario logueado
        self._duplicar_desde_factura_id = duplicar_desde_factura_id
        self._cliente_id = None  # maestro clientes; None = mostrador / sin vínculo

        # Lista de ítems de la factura: cada uno es un dict
        # {
        #   "id": int,
        #   "nombre": str,
        #   "cantidad": float,
        #   "precio": float,
        #   "descuento": float,   # monto de descuento por línea
        #   "subtotal_bruto": float,  # precio * cantidad (antes de descuento)
        #   "subtotal_neto": float    # después de descuento (sin impuestos)
        # }
        self.factura_items = []

        # Totales de la factura
        self.subtotal_bruto = 0.0          # suma de precio * cantidad
        self.descuentos_items_total = 0.0  # suma de descuentos por línea
        self.descuento_global_monto = 0.0  # descuento general
        self.subtotal_total = 0.0          # subtotal neto (bruto - todos los descuentos)
        self.impuestos_total = 0.0         # suma ITBIS líneas
        self.total_factura = 0.0           # gravable + ITBIS − desc. global
        self.propina_monto = 0.0           # 10% de ley, fuera del ITBIS
        self.var_propina = BooleanVar(value=False)
        self.var_puntos = BooleanVar(value=False)
        self.puntos_usados = 0
        self.puntos_rebaja = 0.0
        self._cambio_ticket = 0.0
        self._conduce_id = None
        self._conduce_codigo = ""
        self._conduce_mesa = ""
        self._conduce_usuario = ""

        # Producto actual seleccionado después de una búsqueda
        # Tuple: (..., aplica_itbis)
        self.producto_actual = None

        # Resultados de la última búsqueda (lista de tuplas)
        self.resultados_busqueda = []

        # Referencias a widgets
        self.product_info_label = None
        self.product_image_label = None
        self.entry_buscar = None
        self.entry_cantidad = None
        self.entry_desc_pct = None
        self.entry_desc_monto = None

        self.lista_resultados = None
        self.tree_factura = None

        self.lbl_subtotal = None
        self.lbl_descuentos = None
        self.lbl_total = None

        # referencia a la imagen CTkImage actual
        self.product_ctk_image = None

        self.var_imprimir_doc = BooleanVar(value=True)
        self.var_solo_presupuesto = BooleanVar(value=False)

        self._buscar_after_id = None
        self._num_doc_preview = None
        self._categoria_activa = None
        self._pago_manual = False
        self._sincronizando_pago = False
        self._metodo_rapido = "efectivo"
        self._cat_buttons = {}
        self.grid_inner = None
        self.entry_pago_efectivo = None
        self.entry_pago_tarjeta = None
        self.entry_pago_transferencia = None
        self.lbl_cambio_panel = None
        self.lbl_estado_caja = None
        self.btn_cobrar = None
        self.btn_recuperar = None
        self._metodo_buttons = {}
        self._ultimo_ncf = None
        self.combo_lista = None
        self.entry_pago_otro = None
        self.combo_pago_otro = None
        self._extras_pago = {}

        self._setup_ui()
        if self._duplicar_desde_factura_id:
            self._aplicar_duplicado_desde_factura(self._duplicar_desde_factura_id)

    # ==========================================
    #               UI
    # ==========================================

    def _sql_productos(self) -> str:
        return """
            SELECT id, nombre, precio, precio_base, precio_minimo,
                   stock, codigo_barras, imagen_path,
                   IFNULL(aplica_itbis, 1),
                   IFNULL(facturar_sin_stock, 1),
                   IFNULL(descripcion, ''),
                   IFNULL(descripcion_en_factura, 0),
                   IFNULL(precio_2, 0), IFNULL(precio_3, 0), IFNULL(precio_4, 0),
                   IFNULL(facturar_nivel_precio, 1)
            FROM productos
        """

    def _setup_ui(self):
        # Mostrador: catálogo a la izquierda, ticket y cobro a la derecha.
        main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        emp = self.db.get_empresa_info()
        nom_emp = (emp.get("nombre") or "Punto de venta").strip()

        header = ctk.CTkFrame(main_frame, fg_color="#1e3a5f", corner_radius=8)
        header.pack(fill="x", padx=2, pady=(0, 4))

        top_h = ctk.CTkFrame(header, fg_color="transparent")
        top_h.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(
            top_h,
            text=f"Punto de venta — {nom_emp}",
            font=("Arial", 15, "bold"),
            text_color="#f8fafc",
        ).pack(side="left")
        self.lbl_estado_caja = ctk.CTkLabel(
            top_h,
            text="Caja…",
            font=("Arial", 12, "bold"),
            text_color="#fca5a5",
        )
        self.lbl_estado_caja.pack(side="right", padx=(8, 0))
        ctk.CTkLabel(
            top_h,
            text=f"Cajero: {self.current_user or '—'}",
            font=("Arial", 11),
            text_color="#cbd5e1",
        ).pack(side="right", padx=8)

        hf = ctk.CTkFrame(header, fg_color="transparent")
        hf.pack(fill="x", padx=8, pady=(2, 8))

        ctk.CTkLabel(hf, text="Cliente", font=("Arial", 11)).grid(
            row=0, column=0, sticky="w", padx=4, pady=2
        )
        self.entry_codigo_cliente = ctk.CTkEntry(hf, width=130, height=28)
        self.entry_codigo_cliente.grid(row=0, column=1, padx=2, pady=2)
        self.entry_codigo_cliente.insert(0, "MOSTRADOR")
        ctk.CTkButton(
            hf,
            text="Buscar",
            width=72,
            height=28,
            fg_color="#334155",
            command=self._abrir_dialogo_cliente,
        ).grid(row=0, column=2, padx=2, pady=2)

        ctk.CTkLabel(hf, text="Pago", font=("Arial", 11)).grid(
            row=0, column=3, sticky="w", padx=(10, 4), pady=2
        )
        self._condicion_label_to_id: dict[str, int | None] = {}
        cond_rows = self.db.list_condiciones_pago()
        labels_cond = [f"{r[2]} ({r[1]})" for r in cond_rows]
        self._condicion_label_to_id = {
            f"{r[2]} ({r[1]})": int(r[0]) for r in cond_rows
        }
        if not labels_cond:
            labels_cond = ["— Sin condiciones en catálogo —"]
            self._condicion_label_to_id[labels_cond[0]] = None
        self.combo_condicion_pago = ctk.CTkComboBox(
            hf,
            values=labels_cond,
            width=200,
            height=28,
            command=self._on_condicion_ui,
        )
        self.combo_condicion_pago.set(labels_cond[0])
        self.combo_condicion_pago.grid(row=0, column=4, padx=2, pady=2, sticky="w")

        ctk.CTkLabel(hf, text="Doc.", font=("Arial", 11)).grid(
            row=0, column=5, sticky="w", padx=(10, 4), pady=2
        )
        self.combo_tipo_comprobante = ctk.CTkComboBox(
            hf,
            values=[
                "Consumidor final",
                "Crédito fiscal",
                "Gubernamental",
                "Especial",
            ],
            width=140,
            height=28,
            command=self._on_tipo_comprobante_cambiado,
        )
        self.combo_tipo_comprobante.set("Consumidor final")
        self.combo_tipo_comprobante.grid(row=0, column=6, padx=2, pady=2)
        self.lbl_sec_comprobante = ctk.CTkLabel(
            hf,
            text="",
            font=("Arial", 11, "bold"),
            text_color="#93c5fd",
        )
        self.lbl_sec_comprobante.grid(row=0, column=7, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(hf, text="RNC", font=("Arial", 11)).grid(
            row=1, column=0, sticky="w", padx=4, pady=2
        )
        self.entry_rnc_cliente = ctk.CTkEntry(
            hf, width=130, height=28, placeholder_text="Opcional"
        )
        self.entry_rnc_cliente.grid(row=1, column=1, padx=2, pady=2, sticky="w")
        self.lbl_fecha_emision = ctk.CTkLabel(
            hf,
            text=datetime.now().strftime("%d/%m/%Y"),
            font=("Arial", 11),
            text_color="#cbd5e1",
        )
        self.lbl_fecha_emision.grid(row=1, column=4, sticky="w", padx=2, pady=2)
        self.lbl_vendedor_caja = ctk.CTkLabel(hf, text="")
        ctk.CTkCheckBox(
            hf,
            text="Solo presupuesto",
            variable=self.var_solo_presupuesto,
            font=("Arial", 11),
            command=self._actualizar_boton_cobro,
        ).grid(row=1, column=5, columnspan=2, sticky="w", padx=4, pady=2)
        ctk.CTkButton(
            hf,
            text="Impresora",
            width=90,
            height=28,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: open_config_impresion(
                self.parent.winfo_toplevel(), self.db
            ),
        ).grid(row=1, column=7, padx=4, pady=2, sticky="e")

        self._actualizar_vista_secuencia_documento()

        body = ctk.CTkFrame(main_frame, fg_color="#2B2B2B")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left_frame = ctk.CTkFrame(body, fg_color="#1F1F1F", corner_radius=8)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=2)
        right_frame = ctk.CTkFrame(body, fg_color="#1F1F1F", corner_radius=8)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=2)

        scan = ctk.CTkFrame(left_frame, fg_color="transparent")
        scan.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(scan, text="Código", font=("Arial", 12, "bold")).pack(
            side="left", padx=(0, 6)
        )
        self.entry_buscar = ctk.CTkEntry(
            scan,
            height=36,
            placeholder_text="Escanear código o escribir nombre",
            font=("Arial", 14),
        )
        self.entry_buscar.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.entry_buscar.bind("<Return>", self._on_entrada_venta)
        self.entry_buscar.bind("<KeyRelease>", self._programar_busqueda_en_vivo)
        self.entry_cantidad = ctk.CTkEntry(scan, width=64, height=36, placeholder_text="Cant.")
        self.entry_cantidad.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            scan,
            text="Catálogo",
            width=88,
            height=36,
            fg_color="#334155",
            command=self._abrir_catalogo,
        ).pack(side="left")

        desc_row = ctk.CTkFrame(left_frame, fg_color="transparent")
        desc_row.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(desc_row, text="Desc %", font=("Arial", 11)).pack(side="left")
        self.entry_desc_pct = ctk.CTkEntry(desc_row, width=56, height=28)
        self.entry_desc_pct.pack(side="left", padx=4)
        ctk.CTkLabel(desc_row, text="Desc $", font=("Arial", 11)).pack(side="left", padx=(8, 0))
        self.entry_desc_monto = ctk.CTkEntry(desc_row, width=70, height=28)
        self.entry_desc_monto.pack(side="left", padx=4)
        ctk.CTkLabel(desc_row, text="Lista", font=("Arial", 11)).pack(
            side="left", padx=(10, 0)
        )
        self.combo_lista = ctk.CTkComboBox(
            desc_row,
            values=["Público", "Mayorista", "VIP", "Especial"],
            width=120,
            command=self._on_lista_precio,
        )
        self.combo_lista.set("Público")
        self.combo_lista.pack(side="left", padx=4)
        ctk.CTkLabel(
            desc_row,
            text="Enter agrega y limpia el código",
            font=("Arial", 11),
            text_color="#94a3b8",
        ).pack(side="right")

        self.cat_bar = ctk.CTkFrame(left_frame, fg_color="transparent")
        self.cat_bar.pack(fill="x", padx=6, pady=(0, 4))

        self.grid_scroll = ctk.CTkScrollableFrame(
            left_frame, fg_color="#171717", corner_radius=6
        )
        self.grid_scroll.pack(fill="both", expand=True, padx=8, pady=4)
        self.grid_inner = ctk.CTkFrame(self.grid_scroll, fg_color="transparent")
        self.grid_inner.pack(fill="both", expand=True)

        self.product_info_label = ctk.CTkLabel(
            left_frame,
            text="Toque un producto o escanee el código",
            font=("Arial", 11),
            text_color="#cbd5e1",
            anchor="w",
        )
        self.product_info_label.pack(fill="x", padx=10, pady=(2, 8))
        self.product_image_label = None

        pedido = ctk.CTkFrame(right_frame, fg_color="transparent")
        pedido.pack(fill="x", padx=8, pady=(8, 2))
        ctk.CTkLabel(
            pedido,
            text="Código del mesero",
            font=("Arial", 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        fila_conduce = ctk.CTkFrame(pedido, fg_color="transparent")
        fila_conduce.pack(fill="x", pady=(2, 0))
        self.entry_conduce = ctk.CTkEntry(
            fila_conduce,
            height=28,
            placeholder_text="C0042",
        )
        self.entry_conduce.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.entry_conduce.bind("<Return>", lambda _e: self._traer_conduce())
        self.entry_conduce.bind("<FocusIn>", lambda _e: self._refrescar_cola_conduces())
        ctk.CTkButton(
            fila_conduce,
            text="Traer pedido",
            width=110,
            height=28,
            fg_color="#2563eb",
            command=self._traer_conduce,
        ).pack(side="left")
        self.lbl_conduce = ctk.CTkLabel(
            pedido,
            text="",
            font=("Arial", 11),
            anchor="w",
            text_color="#86efac",
        )
        self.lbl_conduce.pack(fill="x")
        self.cola_conduces = ctk.CTkScrollableFrame(
            pedido, height=78, fg_color="transparent"
        )
        self.cola_conduces.pack(fill="x", pady=(2, 0))
        self._programar_cola_conduces()

        ctk.CTkLabel(
            right_frame,
            text="Ticket",
            font=("Arial", 14, "bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 2))

        tree_container = ctk.CTkFrame(right_frame, fg_color="#1F1F1F")
        tree_container.pack(fill="both", expand=True, padx=8, pady=2)

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "FacturaTreeview.Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=22,
            fieldbackground="#2a2d2e",
            borderwidth=0,
        )
        style.configure(
            "FacturaTreeview.Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 10, "bold"),
        )
        style.map("FacturaTreeview.Treeview", background=[("selected", "#22559b")])

        self.tree_factura = ttk.Treeview(
            tree_container,
            columns=("Nombre", "Cant", "P.Unit", "Desc", "Subt", "ITBIS", "Total"),
            show="headings",
            style="FacturaTreeview.Treeview",
        )
        cols = [
            ("Nombre", 120),
            ("Cant", 48),
            ("P.Unit", 64),
            ("Desc", 52),
            ("Subt", 64),
            ("ITBIS", 56),
            ("Total", 68),
        ]
        for col, width in cols:
            self.tree_factura.heading(col, text=col)
            self.tree_factura.column(col, width=width, anchor="center")
        self.tree_factura.column("Nombre", anchor="w")
        scrollbar_detalle = ttk.Scrollbar(
            tree_container, orient="vertical", command=self.tree_factura.yview
        )
        self.tree_factura.configure(yscrollcommand=scrollbar_detalle.set)
        scrollbar_detalle.pack(side="right", fill="y")
        self.tree_factura.pack(side="left", fill="both", expand=True)
        self.tree_factura.bind("<Double-1>", lambda e: self.editar_item_factura())

        item_btn_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        item_btn_frame.pack(fill="x", padx=8, pady=2)
        ctk.CTkButton(
            item_btn_frame, text="Editar", width=72, command=self.editar_item_factura
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            item_btn_frame,
            text="Quitar",
            width=72,
            fg_color="#c0392b",
            command=self.eliminar_item_factura,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            item_btn_frame,
            text="Vaciar",
            width=72,
            fg_color="#64748b",
            command=self.vaciar_factura,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            item_btn_frame,
            text="Desc. global",
            width=100,
            fg_color="#6d28d9",
            command=self._abrir_descuento_global_dialog,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            item_btn_frame, text="Nota", width=64, command=self._nota_linea
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            item_btn_frame,
            text="Se queda",
            width=80,
            fg_color="#334155",
            command=self._marcar_queda,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            item_btn_frame,
            text="Dejar el resto",
            width=110,
            fg_color="#334155",
            command=self._dejar_resto_en_mesa,
        ).pack(side="left", padx=2)

        totales_frame = ctk.CTkFrame(right_frame, fg_color="#111827", corner_radius=6)
        totales_frame.pack(fill="x", padx=8, pady=4)
        self.lbl_subtotal = ctk.CTkLabel(
            totales_frame, text="Subtotal: RD$ 0.00", font=("Arial", 12), anchor="w"
        )
        self.lbl_subtotal.pack(fill="x", padx=8, pady=(6, 0))
        self.lbl_descuentos = ctk.CTkLabel(
            totales_frame, text="Descuentos: RD$ 0.00", font=("Arial", 12), anchor="w"
        )
        self.lbl_descuentos.pack(fill="x", padx=8)
        self.lbl_impuestos = ctk.CTkLabel(
            totales_frame, text="ITBIS (18%): RD$ 0.00", font=("Arial", 12), anchor="w"
        )
        self.lbl_impuestos.pack(fill="x", padx=8)
        self.lbl_total = ctk.CTkLabel(
            totales_frame,
            text="Total: RD$ 0.00",
            font=("Arial", 18, "bold"),
            text_color="#86efac",
            anchor="w",
        )
        self.lbl_total.pack(fill="x", padx=8, pady=(0, 6))

        pago = ctk.CTkFrame(right_frame, fg_color="#111827", corner_radius=6)
        pago.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(pago, text="Cobro", font=("Arial", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2)
        )
        self._metodo_buttons = {}
        for col, (clave, etiqueta) in enumerate(
            (("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia"))
        ):
            b = ctk.CTkButton(
                pago,
                text=etiqueta,
                height=28,
                fg_color="#334155",
                command=lambda m=clave: self._elegir_metodo_rapido(m),
            )
            b.grid(row=1, column=col, padx=4, pady=2, sticky="ew")
            self._metodo_buttons[clave] = b
        pago.grid_columnconfigure(0, weight=1)
        pago.grid_columnconfigure(1, weight=1)
        pago.grid_columnconfigure(2, weight=1)

        def _fila_monto(fila, texto, attr):
            ctk.CTkLabel(pago, text=texto, font=("Arial", 11)).grid(
                row=fila, column=0, sticky="w", padx=8, pady=2
            )
            ent = ctk.CTkEntry(pago, height=28)
            ent.grid(row=fila, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
            ent.bind("<KeyRelease>", self._on_pago_manual)
            setattr(self, attr, ent)

        _fila_monto(2, "Efectivo recibido", "entry_pago_efectivo")
        _fila_monto(3, "Tarjeta", "entry_pago_tarjeta")
        _fila_monto(4, "Transferencia", "entry_pago_transferencia")
        self.lbl_cambio_panel = ctk.CTkLabel(
            pago,
            text="Cambio: RD$ 0.00",
            font=("Arial", 13, "bold"),
            text_color="#fde68a",
        )
        ctk.CTkLabel(pago, text="Otro", font=("Arial", 11)).grid(
            row=5, column=0, sticky="w", padx=8, pady=2
        )
        extras = [
            (c, n, int(a or 0))
            for c, n, a, _act in self.db.listar_metodos_pago()
            if c not in ("efectivo", "tarjeta", "transferencia")
        ]
        self._extras_pago = {n: (c, af) for c, n, af in extras}
        nombres_extra = ["—"] + list(self._extras_pago)
        self.combo_pago_otro = ctk.CTkComboBox(
            pago,
            values=nombres_extra,
            width=120,
            command=lambda _v: self._elegir_metodo_extra(),
        )
        self.combo_pago_otro.set("—")
        self.combo_pago_otro.grid(row=5, column=1, sticky="ew", padx=(8, 2), pady=2)
        self.entry_pago_otro = ctk.CTkEntry(pago, height=28, width=80)
        self.entry_pago_otro.grid(row=5, column=2, sticky="ew", padx=(2, 8), pady=2)
        self.entry_pago_otro.bind("<KeyRelease>", self._on_pago_manual)
        ctk.CTkCheckBox(
            pago,
            text="10% de ley",
            variable=self.var_propina,
            command=self._recalcular_totales_y_refrescar,
            font=("Arial", 11),
        ).grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=2)
        ctk.CTkLabel(pago, text="Autorización", font=("Arial", 11)).grid(
            row=7, column=0, sticky="w", padx=8, pady=2
        )
        self.entry_auth = ctk.CTkEntry(pago, height=28, placeholder_text="La del voucher")
        self.entry_auth.grid(row=7, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
        ctk.CTkLabel(pago, text="Saldo a favor", font=("Arial", 11)).grid(
            row=8, column=0, sticky="w", padx=8, pady=2
        )
        self.entry_pago_saldo = ctk.CTkEntry(pago, height=28)
        self.entry_pago_saldo.grid(row=8, column=1, columnspan=2, sticky="ew", padx=8, pady=2)
        self.entry_pago_saldo.insert(0, "0.00")
        self.entry_pago_saldo.bind("<KeyRelease>", self._on_pago_manual)
        ctk.CTkCheckBox(
            pago,
            text="Usar puntos",
            variable=self.var_puntos,
            command=self._recalcular_totales_y_refrescar,
            font=("Arial", 11),
        ).grid(row=9, column=0, sticky="w", padx=8, pady=2)
        self.lbl_cliente_saldo = ctk.CTkLabel(
            pago, text="", font=("Arial", 11), anchor="w", text_color="#94a3b8"
        )
        self.lbl_cliente_saldo.grid(row=9, column=1, columnspan=2, sticky="w", padx=4, pady=2)
        self.lbl_cambio_panel.grid(
            row=10, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 6)
        )

        espera_row = ctk.CTkFrame(right_frame, fg_color="transparent")
        espera_row.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkButton(
            espera_row,
            text="En espera",
            width=100,
            height=32,
            fg_color="#334155",
            command=self._poner_en_espera,
        ).pack(side="left", padx=2)
        self.btn_recuperar = ctk.CTkButton(
            espera_row,
            text="Recuperar (0)",
            width=120,
            height=32,
            fg_color="#334155",
            command=self._abrir_esperas,
        )
        self.btn_recuperar.pack(side="left", padx=2)
        ctk.CTkButton(
            espera_row,
            text="Mov. caja",
            width=100,
            height=32,
            fg_color="#b45309",
            command=self._movimiento_caja,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            espera_row,
            text="Devolver",
            width=90,
            height=32,
            fg_color="#7f1d1d",
            command=self._abrir_devolucion,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            espera_row,
            text="NCF",
            width=60,
            height=32,
            fg_color="#334155",
            command=self._abrir_fiscal,
        ).pack(side="left", padx=2)

        acciones = ctk.CTkFrame(right_frame, fg_color="transparent")
        acciones.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(
            acciones,
            text="Pre-cuenta",
            width=100,
            height=32,
            fg_color="#334155",
            command=self._imprimir_precuenta,
        ).pack(side="left", padx=2)
        ctk.CTkCheckBox(
            acciones,
            text="Imprimir ticket",
            variable=self.var_imprimir_doc,
            font=("Arial", 11),
        ).pack(side="left", padx=2)
        self.btn_cobrar = ctk.CTkButton(
            acciones,
            text="Cobrar",
            width=160,
            height=40,
            font=("Arial", 15, "bold"),
            fg_color="#15803d",
            command=self.finalizar_factura,
        )
        self.btn_cobrar.pack(side="right", padx=2)

        try:
            self.parent.bind("<F2>", lambda e: self.finalizar_factura())
            self.parent.bind("<F4>", lambda e: self._poner_en_espera())
            self.parent.bind("<F3>", lambda e: self.entry_buscar.focus_set())
            self.parent.bind("<Delete>", self._on_delete_key)
        except Exception:
            pass

        self._pintar_categorias()
        self._consultar_y_pintar_categoria()
        self._sync_pago_con_total()
        self._actualizar_boton_cobro()
        self._actualizar_btn_espera()
        self._marcar_metodos()
        try:
            self.parent.after(200, self._enfocar_lector)
        except Exception:
            self._enfocar_lector()

    def _on_delete_key(self, event=None):
        try:
            enfocado = self.parent.focus_get()
        except Exception:
            enfocado = None
        campos = (
            self.entry_buscar,
            self.entry_cantidad,
            self.entry_desc_pct,
            self.entry_desc_monto,
            self.entry_pago_efectivo,
            self.entry_pago_tarjeta,
            self.entry_pago_transferencia,
            self.entry_codigo_cliente,
            self.entry_rnc_cliente,
        )
        if enfocado in campos:
            return
        self.eliminar_item_factura()

    def _enfocar_lector(self):
        try:
            if self.entry_buscar is not None:
                self.entry_buscar.focus_set()
        except Exception:
            pass

    def _cancelar_busqueda_pendiente(self):
        if self._buscar_after_id is None:
            return
        try:
            self.parent.after_cancel(self._buscar_after_id)
        except Exception:
            pass
        self._buscar_after_id = None

    def _on_entrada_venta(self, event=None):
        """Enter en el lector: coincidencia exacta suma la cantidad y limpia el código."""
        self._cancelar_busqueda_pendiente()
        texto = (self.entry_buscar.get() or "").strip()
        if not texto:
            return "break"
        row = self._fila_producto_exacta(texto)
        if row is None:
            self.buscar_producto(desde_teclado=True)
            if len(self.resultados_busqueda) == 1:
                row = self.resultados_busqueda[0]
        if row is None:
            return "break"
        antes = len(self.factura_items)
        self._agregar_fila_producto(row)
        if len(self.factura_items) > antes:
            self.entry_buscar.delete(0, "end")
            self._consultar_y_pintar_categoria()
        self._enfocar_lector()
        return "break"

    def _fila_producto_exacta(self, texto: str):
        c = (texto or "").strip()
        if not c:
            return None
        conn = self.db.get_connection()
        cur = conn.cursor()
        clauses = [
            "TRIM(IFNULL(codigo_barras,'')) = ?",
            "TRIM(IFNULL(codigo_producto,'')) = ?",
        ]
        params: list = [c, c]
        if c.isdigit():
            clauses.append("id = ?")
            params.append(int(c))
        cur.execute(
            self._sql_productos()
            + " WHERE IFNULL(activo,1) = 1 AND ("
            + " OR ".join(clauses)
            + ") LIMIT 1",
            params,
        )
        row = cur.fetchone()
        conn.close()
        return row

    def _fetch_producto_por_id(self, pid: int):
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            self._sql_productos() + " WHERE id = ? AND IFNULL(activo,1) = 1",
            (int(pid),),
        )
        row = cur.fetchone()
        conn.close()
        return row

    def _agregar_fila_producto(self, row):
        self.producto_actual = row
        self._mostrar_producto(row)
        self.agregar_a_factura()

    def _listar_categorias(self):
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nombre FROM categorias ORDER BY nombre COLLATE NOCASE"
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def _pintar_categorias(self):
        for w in self.cat_bar.winfo_children():
            w.destroy()
        self._cat_buttons = {}
        todos = ctk.CTkButton(
            self.cat_bar,
            text="Todos",
            height=28,
            width=72,
            fg_color="#334155",
            command=lambda: self._elegir_categoria(None),
        )
        todos.pack(side="left", padx=2, pady=2)
        self._cat_buttons[None] = todos
        for cid, nombre in self._listar_categorias():
            btn = ctk.CTkButton(
                self.cat_bar,
                text=(nombre or "")[:16],
                height=28,
                fg_color="#334155",
                command=lambda c=cid: self._elegir_categoria(c),
            )
            btn.pack(side="left", padx=2, pady=2)
            self._cat_buttons[cid] = btn
        self._marcar_categoria_activa()

    def _marcar_categoria_activa(self):
        for cid, btn in self._cat_buttons.items():
            activo = cid == self._categoria_activa
            btn.configure(fg_color="#2563eb" if activo else "#334155")

    def _elegir_categoria(self, cid):
        self._categoria_activa = cid
        if self.entry_buscar is not None:
            self.entry_buscar.delete(0, "end")
        self._marcar_categoria_activa()
        self._consultar_y_pintar_categoria()
        self._enfocar_lector()

    def _consultar_y_pintar_categoria(self):
        conn = self.db.get_connection()
        cur = conn.cursor()
        sql = self._sql_productos() + " WHERE IFNULL(activo,1) = 1"
        params: list = []
        if self._categoria_activa is not None:
            sql += " AND categoria_id = ?"
            params.append(self._categoria_activa)
        sql += " ORDER BY nombre COLLATE NOCASE LIMIT 48"
        cur.execute(sql, params)
        self.resultados_busqueda = list(cur.fetchall())
        conn.close()
        self._pintar_grilla()

    def _resolver_imagen(self, imagen_path: str | None) -> str:
        ruta = (imagen_path or "").strip()
        if not ruta:
            return ""
        if os.path.isfile(ruta):
            return ruta
        junto = os.path.join(os.path.dirname(os.path.abspath(__file__)), ruta)
        if os.path.isfile(junto):
            return junto
        return ""

    def _foto_grilla(self, imagen_path: str | None):
        ruta = self._resolver_imagen(imagen_path)
        if not ruta:
            return None
        try:
            img = Image.open(ruta)
            foto = ctk.CTkImage(img, size=(46, 46))
        except Exception:
            return None
        self._imagenes_grilla.append(foto)
        return foto

    def _pintar_grilla(self):
        if self.grid_inner is None:
            return
        for w in self.grid_inner.winfo_children():
            w.destroy()
        self._imagenes_grilla = []
        rows = self.resultados_busqueda or []
        columnas = 3
        if not rows:
            ctk.CTkLabel(
                self.grid_inner,
                text="No hay productos en esta vista.",
                text_color="#94a3b8",
            ).grid(row=0, column=0, columnspan=columnas, padx=8, pady=12, sticky="w")
        for i, row in enumerate(rows):
            nombre = row[1] or ""
            precio = self._parse_precio(self._precio_para_venta(row))
            stock = row[5]
            texto = f"{nombre[:22]}\nRD$ {precio:.2f}   ·   {stock:g}"
            foto = self._foto_grilla(row[7] if len(row) > 7 else "")
            btn = ctk.CTkButton(
                self.grid_inner,
                text=texto,
                height=72,
                fg_color="#1e293b",
                hover_color="#334155",
                anchor="w",
                image=foto,
                compound="left",
                command=lambda r=row: self._agregar_fila_producto(r),
            )
            btn.grid(
                row=i // columnas,
                column=i % columnas,
                padx=4,
                pady=4,
                sticky="nsew",
            )
        combos = self.db.listar_combos_venta()
        base = len(rows) if rows else columnas
        for j, combo in enumerate(combos):
            cid, nombre, precio_combo = combo[0], combo[1], combo[2]
            i = base + j
            texto = f"Combo · {(nombre or '')[:18]}\nRD$ {float(precio_combo or 0):.2f}"
            btn = ctk.CTkButton(
                self.grid_inner,
                text=texto,
                height=64,
                fg_color="#3f2a16",
                hover_color="#854d0e",
                anchor="w",
                command=lambda c=cid, n=nombre, p=precio_combo: self._agregar_combo(c, n, p),
            )
            btn.grid(
                row=i // columnas,
                column=i % columnas,
                padx=4,
                pady=4,
                sticky="nsew",
            )
        for c in range(columnas):
            self.grid_inner.grid_columnconfigure(c, weight=1)

    def _on_condicion_ui(self, choice=None):
        if not self._pago_manual:
            self._sync_pago_con_total()
        else:
            self._recalcular_cambio_panel()

    def _elegir_metodo_rapido(self, metodo: str):
        self._metodo_rapido = metodo
        self._pago_manual = False
        if self.combo_pago_otro is not None:
            self.combo_pago_otro.set("—")
        self._marcar_metodos()
        self._sync_pago_con_total()
        self._enfocar_lector()

    def _elegir_metodo_extra(self):
        nombre = self.combo_pago_otro.get() if self.combo_pago_otro is not None else "—"
        info = self._extras_pago.get(nombre)
        if not info:
            return
        self._metodo_rapido = info[0]
        self._pago_manual = False
        self._marcar_metodos()
        self._sync_pago_con_total()

    def _abrir_devolucion(self):
        from devoluciones_facturacion import DevolucionesFacturacionFrame

        top = ctk.CTkToplevel(self.parent.winfo_toplevel())
        top.title("Devolución")
        top.geometry("760x520")
        DevolucionesFacturacionFrame(top, current_user=self.current_user).pack(
            fill="both", expand=True
        )

    def _abrir_fiscal(self):
        from operaciones_pos import open_fiscal_dialog

        open_fiscal_dialog(self.parent.winfo_toplevel(), self.db)

    def _marcar_metodos(self):
        for clave, btn in self._metodo_buttons.items():
            btn.configure(
                fg_color="#2563eb" if clave == self._metodo_rapido else "#334155"
            )

    def _on_pago_manual(self, event=None):
        if getattr(self, "_sincronizando_pago", False):
            return
        self._pago_manual = True
        self._recalcular_cambio_panel()

    def _sync_pago_con_total(self):
        if self.entry_pago_efectivo is None:
            return
        if self._pago_manual:
            self._recalcular_cambio_panel()
            return
        self._sincronizando_pago = True
        try:
            total = self._a_cobrar()
            metodo = self._metodo_rapido or "efectivo"
            montos = {"efectivo": "0.00", "tarjeta": "0.00", "transferencia": "0.00"}
            otro = "0.00"
            if not self._condicion_es_credito_plazo():
                if metodo in montos:
                    montos[metodo] = f"{total:.2f}"
                else:
                    otro = f"{total:.2f}"
            for attr, clave in (
                ("entry_pago_efectivo", "efectivo"),
                ("entry_pago_tarjeta", "tarjeta"),
                ("entry_pago_transferencia", "transferencia"),
            ):
                ent = getattr(self, attr)
                ent.delete(0, "end")
                ent.insert(0, montos[clave])
            if self.entry_pago_otro is not None:
                self.entry_pago_otro.delete(0, "end")
                self.entry_pago_otro.insert(0, otro)
            if getattr(self, "entry_pago_saldo", None) is not None:
                self.entry_pago_saldo.delete(0, "end")
                self.entry_pago_saldo.insert(0, "0.00")
        finally:
            self._sincronizando_pago = False
        self._recalcular_cambio_panel()

    def _recalcular_cambio_panel(self):
        if self.lbl_cambio_panel is None or self.entry_pago_efectivo is None:
            return
        total = self._a_cobrar()
        try:
            ef = _parse_monto_pago(self.entry_pago_efectivo.get())
            tar = _parse_monto_pago(self.entry_pago_tarjeta.get())
            trf = _parse_monto_pago(self.entry_pago_transferencia.get())
            otro = (
                _parse_monto_pago(self.entry_pago_otro.get())
                if self.entry_pago_otro is not None
                else 0.0
            )
            saldo = (
                _parse_monto_pago(self.entry_pago_saldo.get())
                if getattr(self, "entry_pago_saldo", None) is not None
                else 0.0
            )
        except ValueError:
            self.lbl_cambio_panel.configure(text="Cambio: —")
            return
        if self._condicion_es_credito_plazo():
            cobrado = ef + tar + trf + otro + saldo
            pend = max(0.0, total - cobrado)
            self.lbl_cambio_panel.configure(
                text=f"Cobrado: RD$ {cobrado:.2f}   Pendiente: RD$ {pend:.2f}"
            )
            return
        otros = tar + trf + otro + saldo
        por_efectivo = max(0.0, total - otros)
        cambio = max(0.0, ef - por_efectivo)
        self.lbl_cambio_panel.configure(text=f"Cambio: RD$ {cambio:.2f}")

    def _actualizar_boton_cobro(self):
        if self.btn_cobrar is None:
            return
        if self.var_solo_presupuesto.get():
            self.btn_cobrar.configure(text="Guardar presupuesto", fg_color="#1d4ed8")
            if self.lbl_estado_caja is not None:
                self.lbl_estado_caja.configure(
                    text="Presupuesto: no exige turno de caja",
                    text_color="#93c5fd",
                )
            return
        abierta = self.db.fetch_caja_abierta_row()
        if abierta is None:
            if self.lbl_estado_caja is not None:
                self.lbl_estado_caja.configure(
                    text="Caja cerrada — abra el turno para cobrar",
                    text_color="#fca5a5",
                )
            self.btn_cobrar.configure(text="Caja cerrada", fg_color="#991b1b")
        else:
            nombre = (abierta[1] or "Caja").strip()
            if self.lbl_estado_caja is not None:
                self.lbl_estado_caja.configure(
                    text=f"Turno abierto · {nombre}",
                    text_color="#86efac",
                )
            self.btn_cobrar.configure(text="Cobrar", fg_color="#15803d")

    def _otro_pago_actual(self) -> tuple[float, str | None]:
        if self.entry_pago_otro is None or self.combo_pago_otro is None:
            return 0.0, None
        monto = _parse_monto_pago(self.entry_pago_otro.get())
        nombre = (self.combo_pago_otro.get() or "").strip()
        info = self._extras_pago.get(nombre)
        if monto <= 0:
            return 0.0, None
        if not info:
            raise ValueError("Elija el método del monto «Otro».")
        return monto, info[0]

    def _a_cobrar(self) -> float:
        return round(float(self.total_factura or 0) + float(self.propina_monto or 0), 2)

    def _construir_pagos(self, ef: float, tar: float, trf: float, otro: float = 0.0, otro_tipo: str | None = None, auth: str = "", saldo: float = 0.0):
        """
        Arma los pagos a guardar. El efectivo que queda en caja es el neto
        (lo recibido menos el cambio), no el billete entregado por el cliente.
        """
        if ef < 0 or tar < 0 or trf < 0 or otro < 0 or saldo < 0:
            messagebox.showerror("Error", "Los montos deben ser positivos.")
            return None
        if otro > 0 and not otro_tipo:
            messagebox.showerror("Cobro", "Elija el método del monto «Otro».")
            return None
        if saldo > 0:
            if not self._cliente_id:
                messagebox.showerror("Saldo a favor", "Para usar saldo a favor elige el cliente.")
                return None
            disponible = self.db.saldo_favor_cliente(self._cliente_id)
            if saldo > disponible + 0.009:
                messagebox.showerror(
                    "Saldo a favor",
                    f"El cliente solo tiene {disponible:.2f} de saldo a favor.",
                )
                return None
        total = self._a_cobrar()
        exactos = tar + trf + otro + saldo
        total_pagos = ef + exactos
        es_cred = self._condicion_es_credito_plazo()
        if es_cred:
            if total_pagos > total + 0.02:
                messagebox.showerror(
                    "Cobro",
                    "El total cobrado ahora no puede ser mayor que el total de la factura.",
                )
                return None
            ef_guardar = ef
        else:
            if exactos > total + 0.02:
                messagebox.showerror(
                    "Cobro",
                    "Tarjeta, transferencia y otros métodos no pueden superar el total. "
                    "El cambio se calcula solo sobre el efectivo.",
                )
                return None
            if total_pagos < total - 0.01:
                messagebox.showerror(
                    "Pago insuficiente",
                    f"El total de pagos RD$ {total_pagos:.2f} es menor que el total RD$ {total:.2f}",
                )
                return None
            ef_guardar = round(max(0.0, total - exactos), 2)
        self._cambio_ticket = 0.0 if es_cred else round(max(0.0, ef - ef_guardar), 2)
        try:
            codigo_auth = autorizacion_ok(tar, auth)
        except ValueError as exc:
            messagebox.showerror("Tarjeta", str(exc))
            return None
        pagos = []
        if ef_guardar > 0:
            pagos.append({"tipo": "efectivo", "monto": ef_guardar})
        if tar > 0:
            pagos.append({"tipo": "tarjeta", "monto": tar, "referencia": codigo_auth})
        if trf > 0:
            pagos.append({"tipo": "transferencia", "monto": trf})
        if otro > 0 and otro_tipo:
            pagos.append({"tipo": otro_tipo, "monto": round(otro, 2)})
        if saldo > 0:
            pagos.append({"tipo": "saldo_favor", "monto": round(saldo, 2)})
        return pagos

    def _cobrar_desde_panel(self):
        try:
            ef = _parse_monto_pago(self.entry_pago_efectivo.get())
            tar = _parse_monto_pago(self.entry_pago_tarjeta.get())
            trf = _parse_monto_pago(self.entry_pago_transferencia.get())
            otro, otro_tipo = self._otro_pago_actual()
            saldo = (
                _parse_monto_pago(self.entry_pago_saldo.get())
                if getattr(self, "entry_pago_saldo", None) is not None
                else 0.0
            )
        except ValueError as exc:
            messagebox.showerror("Error", str(exc) or "Montos de pago inválidos.")
            return
        if self.var_puntos.get() and not self._cliente_id:
            messagebox.showwarning("Puntos", "Para usar puntos elige el cliente.")
            return
        auth = self.entry_auth.get() if self.entry_auth is not None else ""
        pagos = self._construir_pagos(ef, tar, trf, otro, otro_tipo, auth, saldo)
        if pagos is None:
            return
        self._guardar_y_imprimir_factura(
            pagos, imprimir=bool(self.var_imprimir_doc.get())
        )
        self._enfocar_lector()

    # ==========================================
    #               LÓGICA
    # ==========================================

    def _condicion_pago_id_actual(self) -> int | None:
        lab = self.combo_condicion_pago.get()
        return self._condicion_label_to_id.get(lab)

    def _fecha_vencimiento_iso(self) -> str | None:
        cid = self._condicion_pago_id_actual()
        if cid is None:
            return None
        row = self.db.get_condicion_pago(cid)
        if not row:
            return None
        _i, _c, _n, dias, es_cont = row
        if int(es_cont or 1):
            return None
        d = datetime.now().date() + timedelta(days=int(dias or 0))
        return d.isoformat()

    def _condicion_es_credito_plazo(self) -> bool:
        """True si la condición es venta a plazo (cobro diferido; no exige el total al facturar)."""
        cid = self._condicion_pago_id_actual()
        if cid is None:
            return False
        row = self.db.get_condicion_pago(cid)
        if not row:
            return False
        _i, _c, _n, _dias, es_cont = row
        return int(es_cont or 1) == 0

    def _resolver_cliente_id(self) -> int | None:
        if self._cliente_id is not None:
            return self._cliente_id
        cod = (self.entry_codigo_cliente.get() or "").strip()
        if not cod or cod.upper() == "MOSTRADOR":
            return None
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM clientes
            WHERE TRIM(IFNULL(documento,'')) = ? OR TRIM(nombre) = ?
            LIMIT 1
            """,
            (cod, cod),
        )
        r = cur.fetchone()
        conn.close()
        return int(r[0]) if r else None

    def _abrir_catalogo(self):
        PosCatalogoDialog(
            self.parent.winfo_toplevel(),
            self.db,
            self._on_catalogo_producto,
        )

    def _on_catalogo_producto(self, pid: int):
        row = self._fetch_producto_por_id(pid)
        if row is None:
            messagebox.showwarning("Catálogo", "No se encontró el producto.")
            return
        self._agregar_fila_producto(row)
        self._enfocar_lector()

    def _abrir_dialogo_cliente(self):
        top = ctk.CTkToplevel(self.parent.winfo_toplevel())
        top.title("Clientes")
        top.geometry("580x440")
        top.transient(self.parent.winfo_toplevel())
        top.grab_set()

        bar = ctk.CTkFrame(top, fg_color="#1e293b")
        bar.pack(fill="x", padx=8, pady=8)
        ent = ctk.CTkEntry(bar, width=220, placeholder_text="Nombre o documento")
        ent.pack(side="left", padx=4)

        fr = ctk.CTkFrame(top, fg_color="#0f172a")
        fr.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("id", "nombre", "doc", "tel")
        tree = ttk.Treeview(fr, columns=cols, show="headings", height=14)
        for c, t, w in (
            ("id", "Id", 44),
            ("nombre", "Nombre", 240),
            ("doc", "Documento", 120),
            ("tel", "Teléfono", 100),
        ):
            tree.heading(c, text=t)
            tree.column(c, width=w)
        sy = ttk.Scrollbar(fr, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sy.set)
        tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")

        def cargar(q: str = ""):
            for i in tree.get_children():
                tree.delete(i)
            for r in self.db.buscar_clientes(q, 100):
                tree.insert(
                    "",
                    "end",
                    values=(r[0], r[1], r[2] or "", r[3] or ""),
                )

        def buscar():
            cargar(ent.get().strip())

        ctk.CTkButton(bar, text="Buscar", width=80, command=buscar).pack(
            side="left", padx=4
        )
        ent.bind("<Return>", lambda e: buscar())
        cargar()

        def usar_sel():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Cliente", "Seleccione una fila.", parent=top)
                return
            vals = tree.item(sel[0], "values")
            cid = int(vals[0])
            self._cliente_id = cid
            doc = (vals[2] or "").strip()
            nom = (vals[1] or "").strip()
            self.entry_codigo_cliente.delete(0, "end")
            self.entry_codigo_cliente.insert(0, doc or nom or str(cid))
            self.entry_rnc_cliente.delete(0, "end")
            if doc:
                self.entry_rnc_cliente.insert(0, doc)
            top.destroy()
            self._mostrar_saldo_cliente()
            self._recalcular_totales_y_refrescar()

        tree.bind("<Double-1>", lambda e: usar_sel())

        def alta_rapida():
            d = ctk.CTkToplevel(top)
            d.title("Cliente rápido")
            d.geometry("360x200")
            d.transient(top)
            d.grab_set()
            ctk.CTkLabel(d, text="Nombre").pack(anchor="w", padx=12, pady=(12, 2))
            e_n = ctk.CTkEntry(d, width=300)
            e_n.pack(padx=12)
            ctk.CTkLabel(d, text="Documento (opcional)").pack(anchor="w", padx=12, pady=(8, 2))
            e_doc = ctk.CTkEntry(d, width=300)
            e_doc.pack(padx=12)

            def ok():
                nom = (e_n.get() or "").strip()
                if len(nom) < 2:
                    messagebox.showwarning("Cliente", "Indique el nombre.", parent=d)
                    return
                doc = (e_doc.get() or "").strip() or None
                cid = self.db.crear_cliente_rapido(nom, doc, None)
                self._cliente_id = int(cid)
                self.entry_codigo_cliente.delete(0, "end")
                self.entry_codigo_cliente.insert(0, (doc or nom)[:48])
                self.entry_rnc_cliente.delete(0, "end")
                if doc:
                    self.entry_rnc_cliente.insert(0, doc)
                d.destroy()
                top.destroy()

            ctk.CTkButton(d, text="Crear y usar", fg_color="#059669", command=ok).pack(
                pady=16
            )

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(fill="x", pady=8)
        ctk.CTkButton(
            bf, text="Usar selección", fg_color="#2563eb", command=usar_sel
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="Alta rápida…", fg_color="#475569", command=alta_rapida).pack(
            side="left", padx=4
        )
        ctk.CTkButton(bf, text="Cerrar", fg_color="#64748b", command=top.destroy).pack(
            side="left", padx=4
        )

    def _parse_precio(self, precio):
        """Convierte el precio a float, aunque venga como 'RD$ 120.00'."""
        if isinstance(precio, (int, float)):
            return float(precio)
        if isinstance(precio, str):
            precio = precio.replace("RD$", "").replace("$", "").strip()
            try:
                return float(precio)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _precio_nivel_desde_fila(row):
        """Precio según facturar_nivel_precio (1–4); si el nivel está en 0 usa precio principal."""
        if not row or len(row) < 16:
            try:
                return float(row[2] or 0) if row else 0.0
            except (TypeError, ValueError, IndexError):
                return 0.0
        nivel = max(1, min(4, int(row[15] or 1)))
        precios = [row[2], row[12], row[13], row[14]]
        try:
            p = float(precios[nivel - 1] or 0)
        except (TypeError, ValueError, IndexError):
            p = 0.0
        if p <= 0:
            try:
                p = float(row[2] or 0)
            except (TypeError, ValueError):
                p = 0.0
        return p

    def _nivel_lista(self) -> int:
        texto = "Público"
        if self.combo_lista is not None:
            texto = self.combo_lista.get() or "Público"
        return {"Público": 1, "Mayorista": 2, "VIP": 3, "Especial": 4}.get(texto, 1)

    def _precio_para_venta(self, row):
        """Precio de la lista elegida en el ticket (1 público … 4 especial)."""
        if not row or len(row) < 16:
            return self._precio_nivel_desde_fila(row)
        nivel = self._nivel_lista()
        precios = [row[2], row[12], row[13], row[14]]
        try:
            p = float(precios[nivel - 1] or 0)
        except (TypeError, ValueError, IndexError):
            p = 0.0
        if p <= 0:
            try:
                p = float(row[2] or 0)
            except (TypeError, ValueError):
                p = 0.0
        return p

    def _on_lista_precio(self, _choice=None):
        if self.producto_actual:
            self._mostrar_producto(self.producto_actual)
        self._pintar_grilla()

    # ---------- RENDER VISUAL DE LA LISTA ----------

    def _render_lista_resultados(self):
        if self.grid_inner is not None:
            self._pintar_grilla()
            return
        if self.lista_resultados is None:
            return
        self.lista_resultados.delete("0.0", "end")

        if not self.resultados_busqueda:
            self.lista_resultados.insert("end", "❌ No se encontraron productos.\n")
            return

        selected_id = self.producto_actual[0] if self.producto_actual else None

        for p in self.resultados_busqueda:
            (
                pid,
                nombre,
                precio,
                precio_base,
                precio_minimo,
                stock,
                cb,
                img_path,
                _itb,
                _fs,
                _dsc,
                _def,
                _p2,
                _p3,
                _p4,
                _nv,
            ) = p
            precio_float = self._parse_precio(self._precio_para_venta(p))
            marker = "✔" if pid == selected_id else " "
            line = (
                f"[{marker}] ID:{pid} | {nombre} | "
                f"P.Venta RD$ {precio_float:.2f} | Stock: {stock} | CB:{cb}\n"
            )
            self.lista_resultados.insert("end", line)

    # ----------------- BÚSQUEDA --------------------

    def buscar_producto(self, desde_teclado: bool = False):
        texto = self.entry_buscar.get().strip()

        if not texto:
            self._consultar_y_pintar_categoria()
            return

        modo = "Nombre o descripción (contiene)"
        if getattr(self, "combo_modo_busqueda", None) is not None:
            modo = self.combo_modo_busqueda.get() or modo

        conn = self.db.get_connection()
        cursor = conn.cursor()

        base_sql = """
            SELECT id, nombre, precio, precio_base, precio_minimo,
                   stock, codigo_barras, imagen_path,
                   IFNULL(aplica_itbis, 1),
                   IFNULL(facturar_sin_stock, 1),
                   IFNULL(descripcion, ''),
                   IFNULL(descripcion_en_factura, 0),
                   IFNULL(precio_2, 0), IFNULL(precio_3, 0), IFNULL(precio_4, 0),
                   IFNULL(facturar_nivel_precio, 1)
            FROM productos
            WHERE activo = 1
        """

        if modo == "Nombre (empieza con)":
            cursor.execute(
                base_sql + " AND nombre LIKE ? ORDER BY nombre COLLATE NOCASE LIMIT 40",
                (f"{texto}%",),
            )
        elif modo == "Nombre o descripción (contiene)":
            p = f"%{texto}%"
            cursor.execute(
                base_sql
                + " AND (nombre LIKE ? OR IFNULL(descripcion,'') LIKE ? "
                + "OR IFNULL(codigo_barras,'') LIKE ? OR IFNULL(codigo_producto,'') LIKE ?) "
                + "ORDER BY nombre COLLATE NOCASE LIMIT 48",
                (p, p, p, p),
            )
        elif modo == "Código de barras":
            cursor.execute(
                base_sql
                + """ AND (
                        TRIM(IFNULL(codigo_barras,'')) = ?
                     OR codigo_barras LIKE ?
                    )
                    ORDER BY nombre COLLATE NOCASE LIMIT 20
                """,
                (texto, f"%{texto}%"),
            )
        else:
            # ID o código interno
            try:
                id_interno = int(texto)
            except ValueError:
                id_interno = -1
            cursor.execute(
                base_sql
                + """ AND (
                        id = ?
                     OR TRIM(IFNULL(codigo_producto,'')) = ?
                     OR TRIM(IFNULL(codigo_producto,'')) LIKE ?
                    )
                    ORDER BY nombre COLLATE NOCASE LIMIT 20
                """,
                (id_interno, texto, f"{texto}%"),
            )

        resultados = cursor.fetchall()
        conn.close()

        self.resultados_busqueda = list(resultados)

        if not resultados:
            self.producto_actual = None
            self._render_lista_resultados()
            self._mostrar_producto(None)
            return

        self.producto_actual = resultados[0]
        self._mostrar_producto(self.producto_actual)
        self._render_lista_resultados()

    # ---------- CLIC EN LA LISTA DE RESULTADOS ----------

    def _on_result_click(self, event):
        try:
            index = self.lista_resultados.index(f"@{event.x},{event.y}")
            line_str = str(index).split(".")[0]
            line_start = f"{line_str}.0"
            line_end = f"{line_str}.end"

            line_text = self.lista_resultados.get(line_start, line_end).strip()
            if not line_text or line_text.startswith("❌"):
                return

            id_pos = line_text.find("ID:")
            if id_pos == -1:
                return

            rest = line_text[id_pos + 3:].strip()
            pid_str = rest.split("|")[0].strip()
            pid = int(pid_str)
        except Exception:
            return

        for row in self.resultados_busqueda:
            if row[0] == pid:
                self.producto_actual = row
                self._mostrar_producto(row)
                self._render_lista_resultados()
                break

    # ---------- MOSTRAR PRODUCTO SELECCIONADO ----------

    def _mostrar_producto(self, producto_row):
        """
        Maneja productos con y sin imagen usando CTkImage.
        """
        if not producto_row:
            try:
                if self.product_info_label is not None:
                    self.product_info_label.configure(
                        text="Toque un producto o escanee el código"
                    )
                if self.product_image_label is not None:
                    self.product_image_label.configure(text="Sin imagen", image="")
                    self.product_image_label.image = None
            except Exception:
                pass
            self.product_ctk_image = None
            return

        (
            pid,
            nombre,
            precio,
            precio_base,
            precio_minimo,
            stock,
            cb,
            imagen_path,
            aplica_itbis,
            facturar_sin_stock,
            _descripcion,
            _desc_fact,
            _p2,
            _p3,
            _p4,
            _nivel,
        ) = producto_row
        pv = self._precio_para_venta(producto_row)
        precio_float = self._parse_precio(pv)
        lleva = bool(int(aplica_itbis or 1))
        extra = f" | ITBIS: {'sí' if lleva else 'no'}"
        sin_ex = "sí" if int(facturar_sin_stock or 1) else "no"

        try:
            self.product_info_label.configure(
                text=(
                    f"{nombre} | P.Venta: RD$ {precio_float:.2f}{extra} | "
                    f"Stock: {stock} | Sin exist.: {sin_ex} | CB: {cb}"
                )
            )
        except Exception:
            pass

        if self._resolver_imagen(imagen_path):
            try:
                img = Image.open(self._resolver_imagen(imagen_path))
                self.product_ctk_image = ctk.CTkImage(img, size=(110, 80))
                self.product_image_label.configure(
                    image=self.product_ctk_image,
                    text=""
                )
                self.product_image_label.image = self.product_ctk_image
            except Exception:
                self.product_ctk_image = None
                try:
                    self.product_image_label.configure(
                        text="Error al cargar imagen",
                        image=""
                    )
                except Exception:
                    pass
                self.product_image_label.image = None
        else:
            self.product_ctk_image = None
            if self.product_image_label is not None:
                try:
                    self.product_image_label.configure(text="Sin imagen", image="")
                    self.product_image_label.image = None
                except Exception:
                    pass

    def _aplicar_duplicado_desde_factura(self, factura_id: int):
        data = self.db.get_factura_para_duplicar(factura_id)
        top = self.parent.winfo_toplevel()
        if not data:
            messagebox.showwarning(
                "Duplicar",
                "No se puede duplicar: el documento debe tener líneas con producto asociado.",
                parent=top,
            )
            return
        self._cliente_id = data.get("cliente_id")
        self.factura_items.clear()
        for ln in data["lines"]:
            cant = float(ln["cantidad"])
            precio = float(ln["precio_unitario"])
            desc = float(ln["descuento_item"])
            subtotal_bruto = precio * cant
            subtotal_neto = subtotal_bruto - desc
            imp = float(ln["impuesto_item"])
            total_linea = float(ln["total_linea"])
            aplica = imp > 0.001
            item = {
                "id": ln["producto_id"],
                "nombre": ln["descripcion"],
                "cantidad": cant,
                "precio": precio,
                "descuento": desc,
                "subtotal_bruto": subtotal_bruto,
                "subtotal_neto": subtotal_neto,
                "aplica_itbis": aplica,
                "impuesto_item": imp,
                "total_linea": total_linea,
            }
            self.factura_items.append(item)
        self.entry_codigo_cliente.delete(0, "end")
        self.entry_codigo_cliente.insert(0, data["cliente_codigo"])
        self.combo_tipo_comprobante.set(data["comprobante_label"])
        self.entry_rnc_cliente.delete(0, "end")
        doc = data.get("documento_cliente") or ""
        if doc:
            self.entry_rnc_cliente.insert(0, doc)
        self._recalcular_totales_y_refrescar()
        self._actualizar_vista_secuencia_documento(regenerar=True)

    # ---------- PROMOCIONES / DESCUENTOS AUTOMÁTICOS ----------

    def _calcular_descuento_promocion(self, producto_id, cantidad, precio_unit, subtotal_bruto):
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.tipo_descuento, p.valor
                FROM promociones p
                JOIN promociones_detalle d ON p.id = d.promocion_id
                WHERE p.activo = 1
                  AND (p.aplica_por IS NULL OR p.aplica_por = 'producto')
                  AND d.producto_id = ?
                  AND (p.fecha_inicio IS NULL OR p.fecha_inicio <= datetime('now'))
                  AND (p.fecha_fin IS NULL OR p.fecha_fin >= datetime('now'))
                ORDER BY p.id ASC
                LIMIT 1
                """,
                (producto_id,),
            )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return 0.0

            tipo, valor = row
            valor = float(valor) if valor is not None else 0.0

            if tipo == "porcentaje":
                return subtotal_bruto * (valor / 100.0)
            elif tipo == "fijo":
                return min(subtotal_bruto, valor)
            elif tipo == "2x1":
                free_units = int(cantidad // 2)
                return free_units * precio_unit
            elif tipo == "3x2":
                free_units = int(cantidad // 3)
                return free_units * precio_unit
            else:
                return 0.0
        except Exception:
            return 0.0

    # ---------- AGREGAR A FACTURA ----------

    def agregar_a_factura(self):
        if not self.producto_actual:
            messagebox.showerror("Error", "Primero busca y selecciona un producto.")
            return

        cantidad_texto = (self.entry_cantidad.get() or "").strip() or "1"

        try:
            cantidad = float(cantidad_texto)
        except ValueError:
            messagebox.showerror("Error", "Cantidad inválida.")
            return

        if cantidad <= 0:
            messagebox.showerror("Error", "La cantidad debe ser mayor a 0.")
            return

        (
            pid,
            nombre,
            precio,
            precio_base,
            precio_minimo,
            stock,
            cb,
            imagen_path,
            aplica_itbis,
            facturar_sin_stock,
            descripcion,
            desc_en_factura,
            _p2,
            _p3,
            _p4,
            _nivel,
        ) = self.producto_actual

        precio_unit = self._parse_precio(self._precio_para_venta(self.producto_actual))
        aplica = bool(int(aplica_itbis or 1))
        elegido = self._elegir_extras(int(pid))
        if elegido is None:
            return
        extras, nota_linea = elegido
        precio_unit, extra_txt = precio_con_extras(precio_unit, extras)
        nota_linea = limpiar_nota(nota_linea)

        permitir_sin_stock = int(facturar_sin_stock or 1)
        if permitir_sin_stock == 0 and cantidad > stock:
            messagebox.showerror(
                "Stock insuficiente",
                f"Stock disponible: {stock}. No puedes vender {cantidad}."
            )
            return

        if precio_base is None:
            precio_base = precio_unit
        if precio_minimo is None:
            precio_minimo = precio_unit

        subtotal_bruto = precio_unit * cantidad

        # Descuento manual
        desc_pct_text = self.entry_desc_pct.get().strip()
        desc_monto_text = self.entry_desc_monto.get().strip()

        descuento_linea = 0.0

        if not desc_pct_text and not desc_monto_text:
            descuento_linea = self._calcular_descuento_promocion(
                producto_id=pid,
                cantidad=cantidad,
                precio_unit=precio_unit,
                subtotal_bruto=subtotal_bruto
            )
        else:
            if desc_pct_text:
                try:
                    pct = float(desc_pct_text)
                    if pct < 0:
                        pct = 0.0
                    if pct > 100:
                        pct = 100.0
                    descuento_linea = subtotal_bruto * (pct / 100.0)
                except ValueError:
                    messagebox.showerror("Error", "El descuento % debe ser numérico.")
                    return

            if desc_monto_text:
                try:
                    desc_monto_val = float(desc_monto_text)
                    if desc_monto_val < 0:
                        desc_monto_val = 0.0
                    descuento_linea = max(descuento_linea, desc_monto_val)
                except ValueError:
                    messagebox.showerror("Error", "El descuento $ debe ser numérico.")
                    return

        max_descuento_por_precio_minimo = subtotal_bruto - (precio_minimo * cantidad)
        if max_descuento_por_precio_minimo < 0:
            max_descuento_por_precio_minimo = 0.0

        if descuento_linea > max_descuento_por_precio_minimo:
            descuento_linea = max_descuento_por_precio_minimo

        subtotal_neto = subtotal_bruto - descuento_linea

        impuesto_item = (
            round(subtotal_neto * TASA_ITBIS, 2) if aplica else 0.0
        )
        total_linea = round(subtotal_neto + impuesto_item, 2)

        nombre_linea = self._nombre_con_detalle(nombre, extra_txt, nota_linea)
        if int(desc_en_factura or 0) and (descripcion or "").strip() and not extra_txt and not nota_linea:
            nombre_linea = f"{nombre}\n{(descripcion or '').strip()}"[:900]

        item = {
            "id": pid,
            "nombre": nombre_linea,
            "nombre_base": nombre,
            "extras_txt": extra_txt,
            "nota": nota_linea,
            "cantidad": cantidad,
            "precio": precio_unit,
            "descuento": descuento_linea,
            "subtotal_bruto": subtotal_bruto,
            "subtotal_neto": subtotal_neto,
            "aplica_itbis": aplica,
            "impuesto_item": impuesto_item,
            "total_linea": total_linea,
        }

        fusionado = False
        if not desc_pct_text and not desc_monto_text and not extra_txt and not nota_linea:
            for i, existente in enumerate(self.factura_items):
                if existente.get("nota") or existente.get("extras_txt"):
                    continue
                if existente["id"] != pid:
                    continue
                if abs(float(existente["precio"]) - precio_unit) > 0.001:
                    continue
                promo_vieja = self._calcular_descuento_promocion(
                    pid,
                    existente["cantidad"],
                    precio_unit,
                    precio_unit * float(existente["cantidad"]),
                )
                if abs(float(existente["descuento"]) - promo_vieja) > 0.05:
                    continue
                cantidad = float(existente["cantidad"]) + cantidad
                subtotal_bruto = precio_unit * cantidad
                descuento_linea = self._calcular_descuento_promocion(
                    producto_id=pid,
                    cantidad=cantidad,
                    precio_unit=precio_unit,
                    subtotal_bruto=subtotal_bruto,
                )
                max_descuento_por_precio_minimo = subtotal_bruto - (precio_minimo * cantidad)
                if max_descuento_por_precio_minimo < 0:
                    max_descuento_por_precio_minimo = 0.0
                if descuento_linea > max_descuento_por_precio_minimo:
                    descuento_linea = max_descuento_por_precio_minimo
                subtotal_neto = subtotal_bruto - descuento_linea
                impuesto_item = (
                    round(subtotal_neto * TASA_ITBIS, 2) if aplica else 0.0
                )
                total_linea = round(subtotal_neto + impuesto_item, 2)
                item["cantidad"] = cantidad
                item["descuento"] = descuento_linea
                item["subtotal_bruto"] = subtotal_bruto
                item["subtotal_neto"] = subtotal_neto
                item["impuesto_item"] = impuesto_item
                item["total_linea"] = total_linea
                if permitir_sin_stock == 0 and cantidad > stock:
                    messagebox.showerror(
                        "Stock insuficiente",
                        f"Stock disponible: {stock}. No puedes vender {cantidad}.",
                    )
                    return
                self.factura_items[i] = item
                fusionado = True
                break
        if not fusionado:
            self.factura_items.append(item)
        self._recalcular_totales_y_refrescar()

        self.entry_cantidad.delete(0, "end")
        self.entry_desc_pct.delete(0, "end")
        self.entry_desc_monto.delete(0, "end")
        if not (self.entry_buscar.get() or "").strip():
            self._consultar_y_pintar_categoria()
        self._enfocar_lector()

    def _agregar_combo(self, combo_id, nombre, precio_combo):
        cantidad_texto = (self.entry_cantidad.get() or "").strip() or "1"
        try:
            cantidad = float(cantidad_texto)
        except ValueError:
            messagebox.showerror("Combo", "Cantidad inválida.")
            return
        if cantidad <= 0:
            messagebox.showerror("Combo", "La cantidad debe ser mayor a 0.")
            return
        comps = self.db.componentes_combo(int(combo_id))
        if not comps:
            messagebox.showerror("Combo", "Este combo no tiene componentes.")
            return
        ya = 0.0
        for existente in self.factura_items:
            if existente.get("id") is None and existente.get("nombre") == nombre:
                ya += float(existente["cantidad"])
        for pid, cant_comp, nom, stock, sin_stock in comps:
            need = float(cant_comp or 0) * (cantidad + ya)
            if int(sin_stock or 1) == 0 and need > float(stock or 0):
                messagebox.showerror(
                    "Stock insuficiente",
                    f"{nom}: hay {stock} y el combo pide {need:g}.",
                )
                return
        precio_unit = float(precio_combo or 0)
        subtotal_bruto = precio_unit * cantidad
        impuesto_item = round(subtotal_bruto * TASA_ITBIS, 2)
        total_linea = round(subtotal_bruto + impuesto_item, 2)
        item = {
            "id": None,
            "nombre": nombre,
            "cantidad": cantidad,
            "precio": precio_unit,
            "descuento": 0.0,
            "subtotal_bruto": subtotal_bruto,
            "subtotal_neto": subtotal_bruto,
            "aplica_itbis": True,
            "impuesto_item": impuesto_item,
            "total_linea": total_linea,
            "componentes": [
                {"id": int(pid), "cantidad": float(cant_comp or 0)} for pid, cant_comp, *_ in comps
            ],
            "combo_id": int(combo_id),
        }
        for i, existente in enumerate(self.factura_items):
            if existente.get("nombre") != nombre or existente.get("id") is not None:
                continue
            if abs(float(existente["precio"]) - precio_unit) > 0.001:
                continue
            cantidad = float(existente["cantidad"]) + cantidad
            subtotal_bruto = precio_unit * cantidad
            impuesto_item = round(subtotal_bruto * TASA_ITBIS, 2)
            item["cantidad"] = cantidad
            item["subtotal_bruto"] = subtotal_bruto
            item["subtotal_neto"] = subtotal_bruto
            item["impuesto_item"] = impuesto_item
            item["total_linea"] = round(subtotal_bruto + impuesto_item, 2)
            self.factura_items[i] = item
            break
        else:
            self.factura_items.append(item)
        self._recalcular_totales_y_refrescar()
        self.entry_cantidad.delete(0, "end")
        self._enfocar_lector()

    # ---------- GESTIÓN DE ÍTEMS EN FACTURA ----------

    def _refrescar_tree_factura(self):
        if not self.tree_factura:
            return

        self.tree_factura.delete(*self.tree_factura.get_children())

        for idx, item in enumerate(self.factura_items):
            cant = item["cantidad"]
            precio = item["precio"]
            desc = item["descuento"]
            subt = item["subtotal_neto"]
            imp = float(item.get("impuesto_item") or 0)
            total = float(item.get("total_linea") or subt)

            nombre = item["nombre"]
            if item.get("cobrar") is False:
                nombre = f"Se queda · {nombre}"
            self.tree_factura.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    nombre,
                    f"{cant:.2f}",
                    f"{precio:.2f}",
                    f"{desc:.2f}",
                    f"{subt:.2f}",
                    f"{imp:.2f}",
                    f"{total:.2f}",
                )
            )

    def _recalcular_totales_y_refrescar(self):
        if not self.factura_items:
            self._pago_manual = False
            self.subtotal_bruto = 0.0
            self.descuentos_items_total = 0.0
            self.descuento_global_monto = 0.0
            self.subtotal_total = 0.0
            self.impuestos_total = 0.0
            self.total_factura = 0.0
            self.propina_monto = 0.0
            self.puntos_usados = 0
            self.puntos_rebaja = 0.0
            if self._conduce_id is not None:
                self._olvidar_conduce_en_pantalla()

            self._refrescar_tree_factura()
            self._refrescar_totales_ui(0.0)
            return

        self.subtotal_bruto = sum(i["subtotal_bruto"] for i in self.factura_items)
        self.descuentos_items_total = sum(i["descuento"] for i in self.factura_items)

        descuento_total = self.descuentos_items_total + self.descuento_global_monto

        if descuento_total > self.subtotal_bruto:
            descuento_total = self.subtotal_bruto
            self.descuento_global_monto = max(
                0.0,
                descuento_total - self.descuentos_items_total
            )

        # Base gravable (tras descuentos por línea) e ITBIS
        self.subtotal_total = sum(i["subtotal_neto"] for i in self.factura_items)
        self.impuestos_total = sum(
            float(i.get("impuesto_item") or 0) for i in self.factura_items
        )
        self.puntos_usados = 0
        self.puntos_rebaja = 0.0
        if self.var_puntos.get() and self._cliente_id:
            puntos = self._puntos_del_cliente(self._cliente_id)
            usados, rebaja = rebaja_puntos(puntos, puntos)
            tope = self.subtotal_total
            if rebaja > tope:
                usados = int(tope)
                rebaja = float(usados)
            self.puntos_usados = usados
            self.puntos_rebaja = round(rebaja, 2)
            if self.subtotal_total > 0 and self.puntos_rebaja > 0:
                factor = (self.subtotal_total - self.puntos_rebaja) / self.subtotal_total
                self.impuestos_total = round(self.impuestos_total * factor, 2)
                self.subtotal_total = round(self.subtotal_total - self.puntos_rebaja, 2)
        self.total_factura = (
            self.subtotal_total + self.impuestos_total - self.descuento_global_monto
        )
        base_ley = max(0.0, round(self.subtotal_total - self.descuento_global_monto, 2))
        self.propina_monto = propina_legal(base_ley, bool(self.var_propina.get()))

        self._refrescar_tree_factura()
        self._refrescar_totales_ui(descuento_total + self.puntos_rebaja)

    def _refrescar_totales_ui(self, descuento_total):
        self.lbl_subtotal.configure(
            text=f"Subtotal gravable: RD$ {self.subtotal_total:.2f}"
        )
        self.lbl_descuentos.configure(
            text=f"Descuentos: RD$ {descuento_total:.2f}"
        )
        self.lbl_impuestos.configure(
            text=f"ITBIS (18%): RD$ {self.impuestos_total:.2f}"
        )
        if self.propina_monto > 0:
            self.lbl_total.configure(
                text=(
                    f"Total: RD$ {self.total_factura:.2f}\n"
                    f"10% de ley: RD$ {self.propina_monto:.2f}\n"
                    f"A cobrar: RD$ {self._a_cobrar():.2f}"
                )
            )
        else:
            self.lbl_total.configure(
                text=f"Total: RD$ {self.total_factura:.2f}"
            )
        self._sync_pago_con_total()

    def editar_item_factura(self):
        selected = self.tree_factura.selection()
        if not selected:
            messagebox.showwarning(
                "Selección",
                "Selecciona una línea de la factura para editar."
            )
            return

        idx = int(selected[0])
        item = self.factura_items[idx]

        edit_win = ctk.CTkToplevel(self.parent)
        edit_win.title("Editar ítem de factura")
        edit_win.geometry("360x260")
        edit_win.resizable(False, False)

        ctk.CTkLabel(
            edit_win,
            text=item["nombre"],
            font=("Arial", 14, "bold")
        ).pack(pady=(10, 10))

        frame_cant = ctk.CTkFrame(edit_win)
        frame_cant.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_cant, text="Cantidad:").pack(side="left", padx=5)
        entry_cant = ctk.CTkEntry(frame_cant, width=80)
        entry_cant.pack(side="left", padx=5)
        entry_cant.insert(0, f"{item['cantidad']:.2f}")

        frame_prec = ctk.CTkFrame(edit_win)
        frame_prec.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_prec, text="Precio unit.:").pack(side="left", padx=5)
        entry_prec = ctk.CTkEntry(frame_prec, width=80)
        entry_prec.pack(side="left", padx=5)
        entry_prec.insert(0, f"{item['precio']:.2f}")

        frame_desc = ctk.CTkFrame(edit_win)
        frame_desc.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_desc, text="Descuento $ (línea):").pack(side="left", padx=5)
        entry_desc = ctk.CTkEntry(frame_desc, width=80)
        entry_desc.pack(side="left", padx=5)
        entry_desc.insert(0, f"{item['descuento']:.2f}")

        btn_frame = ctk.CTkFrame(edit_win)
        btn_frame.pack(fill="x", padx=10, pady=15)

        def guardar():
            try:
                nueva_cant = float(entry_cant.get().strip())
                nuevo_prec = float(entry_prec.get().strip())
                nuevo_desc = float(entry_desc.get().strip() or "0")
            except ValueError:
                messagebox.showerror(
                    "Error", "Verifica que cantidad, precio y descuento sean válidos."
                )
                return

            if nueva_cant <= 0 or nuevo_prec < 0 or nuevo_desc < 0:
                messagebox.showerror(
                    "Error", "Cantidad > 0 y valores positivos para precio/descuento."
                )
                return

            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT precio_base, precio_minimo, IFNULL(aplica_itbis, 1)
                FROM productos
                WHERE id = ?
                """,
                (item["id"],)
            )
            row = cursor.fetchone()
            conn.close()

            precio_minimo = row[1] if row and row[1] is not None else nuevo_prec
            aplica = bool(int(row[2] or 1)) if row else True

            subtotal_bruto = nuevo_prec * nueva_cant

            max_desc_por_min = subtotal_bruto - (precio_minimo * nueva_cant)
            if max_desc_por_min < 0:
                max_desc_por_min = 0.0

            if nuevo_desc > max_desc_por_min:
                nuevo_desc = max_desc_por_min

            subtotal_neto = subtotal_bruto - nuevo_desc
            impuesto_item = (
                round(subtotal_neto * TASA_ITBIS, 2) if aplica else 0.0
            )
            total_linea = round(subtotal_neto + impuesto_item, 2)

            item["cantidad"] = nueva_cant
            item["precio"] = nuevo_prec
            item["descuento"] = nuevo_desc
            item["subtotal_bruto"] = subtotal_bruto
            item["subtotal_neto"] = subtotal_neto
            item["aplica_itbis"] = aplica
            item["impuesto_item"] = impuesto_item
            item["total_linea"] = total_linea

            self._recalcular_totales_y_refrescar()
            edit_win.destroy()

        ctk.CTkButton(
            btn_frame,
            text="💾 Guardar",
            fg_color="#27ae60",
            command=guardar
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Cancelar",
            fg_color="#7f8c8d",
            command=edit_win.destroy
        ).pack(side="right", padx=5)

        edit_win.grab_set()
        edit_win.focus_set()

    def eliminar_item_factura(self):
        selected = self.tree_factura.selection()
        if not selected:
            messagebox.showwarning(
                "Selección", "Selecciona una línea de la factura para eliminar."
            )
            return

        idx = int(selected[0])
        item = self.factura_items[idx]

        if not messagebox.askyesno(
            "Confirmar",
            f"¿Eliminar '{item['nombre']}' de la factura?"
        ):
            return

        self.factura_items.pop(idx)
        self._recalcular_totales_y_refrescar()

    def vaciar_factura(self):
        if not self.factura_items:
            return
        if not messagebox.askyesno(
            "Confirmar",
            "¿Vaciar toda la factura?"
        ):
            return

        self.factura_items.clear()
        self._recalcular_totales_y_refrescar()
        self._actualizar_vista_secuencia_documento(regenerar=True)

    # ---------- DESCUENTO GLOBAL ----------

    def _abrir_descuento_global_dialog(self):
        if not self.factura_items:
            messagebox.showwarning(
                "Descuento global",
                "No hay ítems en la factura."
            )
            return

        self._recalcular_totales_y_refrescar()
        desc_actual = self.descuento_global_monto
        subtotal_bruto = self.subtotal_bruto
        desc_items = self.descuentos_items_total

        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("Descuento global")
        dlg.geometry("380x260")
        dlg.resizable(False, False)

        info = (
            f"Subt. bruto (sin desc): RD$ {subtotal_bruto:.2f}\n"
            f"Desc. por ítems: RD$ {desc_items:.2f}\n"
            f"Desc. global actual: RD$ {desc_actual:.2f}"
        )

        ctk.CTkLabel(
            dlg,
            text=info,
            font=("Arial", 12),
            justify="left"
        ).pack(pady=(10, 10), padx=10)

        frame_pct = ctk.CTkFrame(dlg)
        frame_pct.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_pct, text="Descuento % (global):").pack(side="left", padx=5)
        entry_pct = ctk.CTkEntry(frame_pct, width=80)
        entry_pct.pack(side="left", padx=5)
        entry_pct.insert(0, "")

        frame_monto = ctk.CTkFrame(dlg)
        frame_monto.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_monto, text="Descuento $ (global):").pack(side="left", padx=5)
        entry_monto = ctk.CTkEntry(frame_monto, width=80)
        entry_monto.pack(side="left", padx=5)
        entry_monto.insert(0, "")

        btn_frame = ctk.CTkFrame(dlg)
        btn_frame.pack(fill="x", padx=10, pady=15)

        def aplicar():
            pct_txt = entry_pct.get().strip()
            monto_txt = entry_monto.get().strip()

            nuevo_desc = 0.0

            try:
                if pct_txt:
                    pct_val = float(pct_txt)
                    if pct_val < 0:
                        pct_val = 0.0
                    if pct_val > 100:
                        pct_val = 100.0
                    nuevo_desc = subtotal_bruto * (pct_val / 100.0)
                if monto_txt:
                    monto_val = float(monto_txt)
                    if monto_val < 0:
                        monto_val = 0.0
                    nuevo_desc = max(nuevo_desc, monto_val)
            except ValueError:
                messagebox.showerror(
                    "Error", "Descuento global debe ser numérico."
                )
                return

            max_desc_posible = subtotal_bruto - desc_items
            if nuevo_desc > max_desc_posible:
                nuevo_desc = max_desc_posible

            self.descuento_global_monto = nuevo_desc
            self._recalcular_totales_y_refrescar()
            dlg.destroy()

        ctk.CTkButton(
            btn_frame,
            text="Aplicar",
            fg_color="#27ae60",
            command=aplicar
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Quitar descuento global",
            fg_color="#c0392b",
            command=lambda: self._quitar_descuento_global(dlg)
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Cancelar",
            fg_color="#7f8c8d",
            command=dlg.destroy
        ).pack(side="right", padx=5)

        dlg.grab_set()
        dlg.focus_set()

    def _quitar_descuento_global(self, dialog):
        self.descuento_global_monto = 0.0
        self._recalcular_totales_y_refrescar()
        dialog.destroy()

    # ==========================
    #   NÚMERO DE FACTURA
    # ==========================

    def _generar_numero_factura(self):
        """
        Genera un número de factura simple basado en fecha/hora.
        """
        ahora = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"F-{ahora}"

    def _on_tipo_comprobante_cambiado(self, choice=None):
        """Al cambiar tipo de comprobante, refresca la vista del próximo número."""
        self._actualizar_vista_secuencia_documento()

    def _actualizar_vista_secuencia_documento(self, regenerar: bool = False):
        """Muestra junto al comprobante el próximo número interno (referencia al guardar)."""
        if getattr(self, "lbl_sec_comprobante", None) is None:
            return
        if regenerar or not self._num_doc_preview:
            self._num_doc_preview = self._generar_numero_factura()
        self.lbl_sec_comprobante.configure(text=f"N° próximo: {self._num_doc_preview}")

    def _programar_busqueda_en_vivo(self, event=None):
        if event and getattr(event, "keysym", "") in ("Up", "Down", "Left", "Right", "Tab"):
            return
        if self._buscar_after_id is not None:
            try:
                self.parent.after_cancel(self._buscar_after_id)
            except Exception:
                pass
        self._buscar_after_id = self.parent.after(220, self._ejecutar_busqueda_en_vivo)

    def _ejecutar_busqueda_en_vivo(self):
        self._buscar_after_id = None
        self.buscar_producto(desde_teclado=True)

    def _actualizar_btn_espera(self):
        if self.btn_recuperar is None:
            return
        try:
            n = self.db.contar_ventas_espera()
        except Exception:
            n = 0
        self.btn_recuperar.configure(text=f"Recuperar ({n})")

    def _nombre_con_detalle(self, base: str, extras_txt: str, nota: str) -> str:
        detalle = " · ".join(
            parte for parte in ((extras_txt or "").strip(), (nota or "").strip()) if parte
        )
        if not detalle:
            return base
        return f"{base} · {detalle}"[:900]

    def _elegir_extras(self, producto_id: int):
        filas = self.db.modificadores_de(producto_id)
        if not filas:
            return [], ""
        top = self.parent.winfo_toplevel()
        dlg = ctk.CTkToplevel(top)
        dlg.title("Extras")
        dlg.geometry("360x420")
        dlg.transient(top)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text="Extras y nota", font=("Arial", 14, "bold")).pack(
            anchor="w", padx=16, pady=(12, 6)
        )
        marcas = []
        for mid, nom, precio in filas:
            var = BooleanVar(value=False)
            ctk.CTkCheckBox(
                dlg,
                text=f"{nom}   RD$ {float(precio):.2f}",
                variable=var,
            ).pack(anchor="w", padx=16, pady=2)
            marcas.append((var, {"id": int(mid), "nombre": nom, "precio": float(precio or 0)}))
        ctk.CTkLabel(dlg, text="Nota", font=("Arial", 11)).pack(anchor="w", padx=16, pady=(8, 2))
        entry = ctk.CTkEntry(dlg, width=300, placeholder_text="Sin hielo, para llevar…")
        entry.pack(anchor="w", padx=16)
        elegido = {"ok": False}

        def aceptar():
            elegido["ok"] = True
            elegido["nota"] = entry.get()
            elegido["extras"] = [dato for var, dato in marcas if var.get()]
            dlg.destroy()

        ctk.CTkButton(dlg, text="Agregar", fg_color="#2563eb", command=aceptar).pack(
            anchor="w", padx=16, pady=12
        )
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        entry.focus_set()
        dlg.wait_window()
        if not elegido["ok"]:
            return None
        return elegido["extras"], limpiar_nota(elegido["nota"])

    def _nota_linea(self):
        selected = self.tree_factura.selection()
        if not selected:
            messagebox.showwarning("Nota", "Selecciona una línea.")
            return
        item = self.factura_items[int(selected[0])]
        top = self.parent.winfo_toplevel()
        dlg = ctk.CTkToplevel(top)
        dlg.title("Nota")
        dlg.geometry("360x150")
        dlg.transient(top)
        dlg.grab_set()
        entry = ctk.CTkEntry(dlg, width=320, placeholder_text="Sin hielo, para llevar…")
        entry.pack(anchor="w", padx=16, pady=16)
        if item.get("nota"):
            entry.insert(0, item["nota"])

        def guardar():
            nota = limpiar_nota(entry.get())
            base = item.get("nombre_base") or item["nombre"]
            item["nombre_base"] = base
            item["nota"] = nota
            item["nombre"] = self._nombre_con_detalle(base, item.get("extras_txt") or "", nota)
            dlg.destroy()
            self._refrescar_tree_factura()

        ctk.CTkButton(dlg, text="Guardar", command=guardar).pack(anchor="w", padx=16)
        entry.focus_set()

    def _marcar_queda(self):
        selected = self.tree_factura.selection()
        if not selected:
            messagebox.showwarning("Mesa", "Selecciona la línea que se queda.")
            return
        item = self.factura_items[int(selected[0])]
        item["cobrar"] = item.get("cobrar") is False
        self._refrescar_tree_factura()

    def _linea_conduce_desde_ticket(self, item: dict) -> dict:
        es_combo = bool(item.get("componentes"))
        return {
            "id": item.get("combo_id") if es_combo else item.get("id"),
            "nombre": item["nombre"],
            "cantidad": item["cantidad"],
            "precio": item["precio"],
            "precio_base": item["precio"],
            "total": round(float(item.get("total_linea") or 0), 2),
            "combo": es_combo,
            "itbis": bool(item.get("aplica_itbis", True)),
            "nota": item.get("nota") or "",
            "extras": [],
        }

    def _dejar_resto_en_mesa(self):
        if not self._conduce_id:
            messagebox.showwarning("Mesa", "Trae primero el pedido de la mesa.")
            return
        indices = [i for i, item in enumerate(self.factura_items) if item.get("cobrar") is not False]
        try:
            van, quedan = partir_lineas(self.factura_items, indices)
        except ValueError as exc:
            messagebox.showwarning("Mesa", str(exc))
            return
        lineas = [self._linea_conduce_desde_ticket(item) for item in quedan]
        subtotal = round(sum(float(item.get("subtotal_neto") or 0) for item in quedan), 2)
        itbis = round(sum(float(item.get("impuesto_item") or 0) for item in quedan), 2)
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE conduces SET items_json=?, subtotal=?, itbis=?, total=? WHERE id=? AND estado='pendiente'",
                (json.dumps(lineas, ensure_ascii=False), subtotal, itbis, round(subtotal + itbis, 2), int(self._conduce_id)),
            )
            if cur.rowcount != 1:
                raise ValueError("Ese pedido ya no está pendiente")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            messagebox.showerror("Mesa", str(exc))
            return
        finally:
            conn.close()
        codigo = self._conduce_codigo
        mesa = self._conduce_mesa
        self.factura_items = list(van)
        self._olvidar_conduce_en_pantalla()
        if getattr(self, "lbl_conduce", None) is not None:
            self.lbl_conduce.configure(
                text=f"Lo que se queda sigue en {codigo} · {mesa}. Cobra ahora esta parte."
            )
        self._recalcular_totales_y_refrescar()
        self._refrescar_cola_conduces()

    def _puntos_del_cliente(self, cliente_id: int) -> int:
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT IFNULL(puntos,0) FROM clientes WHERE id=?", (int(cliente_id),))
        fila = cur.fetchone()
        conn.close()
        return int((fila or [0])[0] or 0)

    def _mostrar_saldo_cliente(self):
        etiqueta = getattr(self, "lbl_cliente_saldo", None)
        if etiqueta is None:
            return
        if not self._cliente_id:
            etiqueta.configure(text="")
            return
        puntos = self._puntos_del_cliente(self._cliente_id)
        saldo = self.db.saldo_favor_cliente(self._cliente_id)
        etiqueta.configure(text=f"{puntos} puntos · saldo RD$ {saldo:.2f}")

    def _olvidar_conduce_en_pantalla(self, limpiar_codigo: bool = True):
        """Suelta el pedido en pantalla. En la base sigue pendiente hasta que se cobre."""
        self._conduce_id = None
        self._conduce_codigo = ""
        self._conduce_mesa = ""
        self._conduce_usuario = ""
        if getattr(self, "lbl_conduce", None) is not None:
            self.lbl_conduce.configure(text="")
        if limpiar_codigo and getattr(self, "entry_conduce", None) is not None:
            self.entry_conduce.delete(0, "end")

    def _programar_cola_conduces(self):
        cola = getattr(self, "cola_conduces", None)
        if cola is None:
            return
        try:
            if not cola.winfo_exists():
                return
        except Exception:
            return
        self._refrescar_cola_conduces()
        try:
            self.parent.after(8000, self._programar_cola_conduces)
        except Exception:
            return

    def _refrescar_cola_conduces(self):
        cola = getattr(self, "cola_conduces", None)
        if cola is None:
            return
        try:
            if not cola.winfo_exists():
                return
        except Exception:
            return
        for hijo in cola.winfo_children():
            hijo.destroy()
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, codigo, mesa, IFNULL(usuario,''), total
                FROM conduces
                WHERE estado = 'pendiente' AND TRIM(IFNULL(codigo,'')) != ''
                ORDER BY datetime(fecha) ASC, id ASC
                LIMIT 30
                """
            )
            filas = cur.fetchall()
        except Exception:
            filas = []
        finally:
            conn.close()
        if not filas:
            ctk.CTkLabel(
                cola,
                text="Ningún pedido por cobrar.",
                font=("Arial", 10),
                text_color="#64748b",
                anchor="w",
            ).pack(fill="x")
            return
        for cid, codigo, mesa, usuario, total in filas:
            quien = (usuario or "Mesero").split("@")[0]
            texto = f"{mesa} · {codigo} · {quien} · RD$ {float(total or 0):.2f}"
            activo = self._conduce_id == cid
            ctk.CTkButton(
                cola,
                text=texto,
                height=26,
                anchor="w",
                fg_color="#14532d" if activo else "#334155",
                hover_color="#166534" if activo else "#1e293b",
                command=lambda c=codigo: self._traer_conduce(c),
            ).pack(fill="x", pady=1)

    def _traer_conduce(self, codigo: str | None = None):
        if self.db.fetch_caja_abierta_row() is None:
            messagebox.showwarning(
                "Caja",
                "Abre la caja antes de traer el pedido del mesero.",
            )
            return
        clave = normalizar_codigo_conduce(
            codigo if codigo is not None else self.entry_conduce.get()
        )
        if not clave:
            return
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.id, c.codigo, c.mesa, IFNULL(c.usuario,''), c.items_json,
                       c.estado, IFNULL(f.numero,'')
                FROM conduces c
                LEFT JOIN facturas f ON f.id = c.factura_id
                WHERE c.codigo = ?
                """,
                (clave,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            messagebox.showwarning("Pedido", "No hay un pedido con ese código.")
            return
        conduce_id, codigo_real, mesa, usuario, items_json, estado, factura = row
        if estado != "pendiente":
            detalle = f": {factura}" if factura else ""
            messagebox.showinfo("Pedido", f"Ese código ya se facturó{detalle}.")
            return
        if self.factura_items and self._conduce_id != conduce_id:
            messagebox.showwarning(
                "Pedido",
                f"Este ticket ya tiene productos. Déjalo en cero antes de traer {codigo_real} · {mesa}.",
            )
            return
        try:
            lineas = json.loads(items_json or "[]")
        except json.JSONDecodeError:
            messagebox.showerror("Pedido", "No se pudo leer el pedido del mesero.")
            return
        items = []
        for linea in lineas:
            if not isinstance(linea, dict):
                continue
            componentes = None
            if linea.get("combo") and linea.get("id"):
                comps = self.db.componentes_combo(int(linea["id"]))
                componentes = [
                    {"id": int(pid), "cantidad": float(cant or 0)}
                    for pid, cant, *_resto in comps
                ]
            item = linea_ticket_desde_conduce(linea, componentes)
            if item:
                items.append(item)
        if not items:
            messagebox.showwarning("Pedido", "Ese pedido no tiene productos.")
            return
        self.factura_items = items
        self._conduce_id = int(conduce_id)
        self._conduce_codigo = codigo_real or clave
        self._conduce_mesa = mesa or ""
        self._conduce_usuario = usuario or ""
        self.descuento_global_monto = 0.0
        self._pago_manual = False
        self.entry_conduce.delete(0, "end")
        self.entry_conduce.insert(0, self._conduce_codigo)
        quien = (usuario or "el mesero").split("@")[0]
        self.lbl_conduce.configure(
            text=f"{self._conduce_codigo} de {quien} · {mesa}. Cobra en esta caja para facturar."
        )
        self._recalcular_totales_y_refrescar()
        self._refrescar_cola_conduces()
        self._enfocar_lector()

    def _limpiar_ticket_en_pantalla(self):
        self.factura_items.clear()
        self._cliente_id = None
        self.descuento_global_monto = 0.0
        self._pago_manual = False
        self.var_solo_presupuesto.set(False)
        self._olvidar_conduce_en_pantalla()
        if self.entry_codigo_cliente is not None:
            self.entry_codigo_cliente.delete(0, "end")
            self.entry_codigo_cliente.insert(0, "MOSTRADOR")
        if self.entry_rnc_cliente is not None:
            self.entry_rnc_cliente.delete(0, "end")
        self.entry_cantidad.delete(0, "end")
        self.entry_desc_pct.delete(0, "end")
        self.entry_desc_monto.delete(0, "end")
        self._recalcular_totales_y_refrescar()
        self._actualizar_vista_secuencia_documento(regenerar=True)
        self._refrescar_cola_conduces()
        self._mostrar_saldo_cliente()
        self._enfocar_lector()

    def _poner_en_espera(self):
        if not self.factura_items:
            messagebox.showwarning("En espera", "El ticket está vacío.")
            self._enfocar_lector()
            return
        self._recalcular_totales_y_refrescar()
        top = self.parent.winfo_toplevel()
        dlg = ctk.CTkToplevel(top)
        dlg.title("Poner en espera")
        dlg.geometry("420x180")
        dlg.resizable(False, False)
        dlg.transient(top)
        dlg.grab_set()
        sugerida = (self.entry_codigo_cliente.get() or "").strip()
        if not sugerida or sugerida.upper() == "MOSTRADOR":
            sugerida = "Espera " + datetime.now().strftime("%H:%M")
        ctk.CTkLabel(
            dlg,
            text="Nombre para recuperar este ticket",
            font=("Arial", 13, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 6))
        entry = ctk.CTkEntry(dlg, width=360)
        entry.pack(anchor="w", padx=16)
        entry.insert(0, sugerida)
        entry.select_range(0, "end")

        def guardar():
            etiqueta = (entry.get() or "").strip()
            if len(etiqueta) < 2:
                messagebox.showwarning(
                    "En espera", "Indique un nombre para el ticket.", parent=dlg
                )
                return
            if self._conduce_id:
                payload = json.dumps(
                    {
                        "items": self.factura_items,
                        "conduce_id": self._conduce_id,
                        "conduce_codigo": self._conduce_codigo,
                        "conduce_mesa": self._conduce_mesa,
                        "conduce_usuario": self._conduce_usuario,
                    },
                    ensure_ascii=False,
                )
            else:
                payload = json.dumps(self.factura_items, ensure_ascii=False)
            self.db.guardar_venta_espera(
                etiqueta,
                self.current_user,
                self._cliente_id,
                self.entry_codigo_cliente.get(),
                self.entry_rnc_cliente.get(),
                self._condicion_pago_id_actual(),
                self.combo_tipo_comprobante.get(),
                self.descuento_global_monto,
                payload,
                self.total_factura,
            )
            dlg.destroy()
            self._limpiar_ticket_en_pantalla()
            self._actualizar_btn_espera()

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(btns, text="Guardar", fg_color="#2563eb", command=guardar).pack(
            side="left"
        )
        ctk.CTkButton(
            btns, text="Cancelar", fg_color="#64748b", command=dlg.destroy
        ).pack(side="left", padx=8)
        entry.focus_set()

    def _abrir_esperas(self):
        filas = self.db.listar_ventas_espera()
        top = self.parent.winfo_toplevel()
        if not filas:
            messagebox.showinfo(
                "En espera",
                "No hay tickets en espera.",
                parent=top,
            )
            self._enfocar_lector()
            return
        dlg = ctk.CTkToplevel(top)
        dlg.title("Tickets en espera")
        dlg.geometry("640x360")
        dlg.transient(top)
        dlg.grab_set()
        ctk.CTkLabel(
            dlg,
            text="Elija un ticket para volver a venderlo",
            font=("Arial", 13, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 6))
        fr = ctk.CTkFrame(dlg)
        fr.pack(fill="both", expand=True, padx=12, pady=4)
        cols = ("etiqueta", "cliente", "total", "usuario", "hora")
        tree = ttk.Treeview(fr, columns=cols, show="headings", height=10)
        for c, t, w in (
            ("etiqueta", "Nombre", 180),
            ("cliente", "Cliente", 120),
            ("total", "Total", 90),
            ("usuario", "Cajero", 90),
            ("hora", "Hora", 120),
        ):
            tree.heading(c, text=t)
            tree.column(c, width=w)
        tree.pack(side="left", fill="both", expand=True)
        for eid, etiqueta, usuario, total, fecha, cliente in filas:
            tree.insert(
                "",
                "end",
                iid=str(eid),
                values=(
                    etiqueta,
                    cliente or "—",
                    f"RD$ {float(total or 0):.2f}",
                    usuario or "—",
                    str(fecha or "")[:16],
                ),
            )

        def _sel():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning(
                    "En espera", "Seleccione un ticket.", parent=dlg
                )
                return None
            return int(sel[0])

        def recuperar():
            eid = _sel()
            if eid is None:
                return
            if self.factura_items and not messagebox.askyesno(
                "En espera",
                "Hay un ticket en curso. Se reemplazará por el de espera.\n\n"
                "¿Continuar?",
                parent=dlg,
            ):
                return
            self._recuperar_espera(eid)
            dlg.destroy()

        def descartar():
            eid = _sel()
            if eid is None:
                return
            if not messagebox.askyesno(
                "En espera",
                "¿Descartar este ticket en espera? No se cobra ni mueve inventario.",
                parent=dlg,
            ):
                return
            self.db.eliminar_venta_espera(eid)
            tree.delete(str(eid))
            self._actualizar_btn_espera()
            if not tree.get_children():
                dlg.destroy()

        bf = ctk.CTkFrame(dlg, fg_color="transparent")
        bf.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(bf, text="Recuperar", fg_color="#2563eb", command=recuperar).pack(
            side="left"
        )
        ctk.CTkButton(
            bf, text="Descartar", fg_color="#b45309", command=descartar
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="Cerrar", fg_color="#64748b", command=dlg.destroy).pack(
            side="right"
        )
        tree.bind("<Double-1>", lambda e: recuperar())

    def _recuperar_espera(self, espera_id: int):
        row = self.db.obtener_venta_espera(espera_id)
        if row is None:
            messagebox.showwarning("En espera", "Ese ticket ya no está disponible.")
            self._actualizar_btn_espera()
            return
        (
            _eid,
            _etiqueta,
            _usuario,
            cliente_id,
            cliente_codigo,
            documento,
            condicion_id,
            tipo_comp,
            desc_global,
            items_json,
            _total,
            _fecha,
        ) = row
        try:
            crudo = json.loads(items_json or "[]")
        except json.JSONDecodeError:
            messagebox.showerror("En espera", "No se pudo leer el ticket guardado.")
            return
        conduce = crudo if isinstance(crudo, dict) else None
        items = (conduce or {}).get("items") if conduce else crudo
        if not isinstance(items, list) or not items:
            messagebox.showwarning("En espera", "El ticket guardado no tiene productos.")
            return
        self._olvidar_conduce_en_pantalla()
        self.factura_items = list(items)
        if conduce and conduce.get("conduce_id"):
            self._conduce_id = int(conduce["conduce_id"])
            self._conduce_codigo = str(conduce.get("conduce_codigo") or "")
            self._conduce_mesa = str(conduce.get("conduce_mesa") or "")
            self._conduce_usuario = str(conduce.get("conduce_usuario") or "")
            if getattr(self, "entry_conduce", None) is not None and self._conduce_codigo:
                self.entry_conduce.insert(0, self._conduce_codigo)
            if getattr(self, "lbl_conduce", None) is not None:
                quien = (self._conduce_usuario or "el mesero").split("@")[0]
                self.lbl_conduce.configure(
                    text=f"{self._conduce_codigo} de {quien} · {self._conduce_mesa}. Cobra en esta caja para facturar."
                )
        self._cliente_id = int(cliente_id) if cliente_id else None
        self.descuento_global_monto = float(desc_global or 0)
        self._pago_manual = False
        self.entry_codigo_cliente.delete(0, "end")
        self.entry_codigo_cliente.insert(0, (cliente_codigo or "MOSTRADOR"))
        self.entry_rnc_cliente.delete(0, "end")
        if documento:
            self.entry_rnc_cliente.insert(0, documento)
        if tipo_comp:
            try:
                self.combo_tipo_comprobante.set(tipo_comp)
            except Exception:
                pass
        if condicion_id is not None:
            for lab, cid in self._condicion_label_to_id.items():
                if cid == int(condicion_id):
                    self.combo_condicion_pago.set(lab)
                    break
        self.db.eliminar_venta_espera(espera_id)
        self._recalcular_totales_y_refrescar()
        self._actualizar_btn_espera()
        self._enfocar_lector()

    def _movimiento_caja(self):
        top = self.parent.winfo_toplevel()
        if self.db.fetch_caja_abierta_row() is None:
            messagebox.showwarning(
                "Caja cerrada",
                "Abra el turno en el módulo Caja antes de registrar un ingreso o un retiro.",
                parent=top,
            )
            self._enfocar_lector()
            return
        dlg = ctk.CTkToplevel(top)
        dlg.title("Movimiento de efectivo")
        dlg.geometry("360x140")
        dlg.resizable(False, False)
        dlg.transient(top)
        dlg.grab_set()
        ctk.CTkLabel(
            dlg,
            text="¿Qué movimiento registra?",
            font=("Arial", 13, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        def elegir(tipo):
            dlg.destroy()
            abrir_dialogo_movimiento_efectivo(
                top, self.db, self.current_user, tipo
            )
            self._actualizar_boton_cobro()
            self._enfocar_lector()

        bf = ctk.CTkFrame(dlg, fg_color="transparent")
        bf.pack(fill="x", padx=16)
        ctk.CTkButton(
            bf, text="Ingreso", fg_color="#15803d", command=lambda: elegir("ingreso")
        ).pack(side="left")
        ctk.CTkButton(
            bf, text="Retiro", fg_color="#b45309", command=lambda: elegir("retiro")
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="Cancelar", fg_color="#64748b", command=dlg.destroy).pack(
            side="right"
        )

    # ==========================
    #   FLUJO DE FINALIZAR
    # ==========================

    def finalizar_factura(self):
        """
        Valida que haya ítems y abre el diálogo de pago.
        """
        if not self.factura_items:
            messagebox.showerror("Error", "La factura está vacía.")
            return

        # recalcular por si hubo cambios recientes
        self._recalcular_totales_y_refrescar()

        if self.total_factura <= 0:
            messagebox.showerror("Error", "Total de la factura inválido.")
            return

        if self.var_solo_presupuesto.get():
            self._guardar_presupuesto()
            self._enfocar_lector()
            return

        if self.db.fetch_caja_abierta_row() is None:
            messagebox.showwarning(
                "Caja cerrada",
                "No hay un turno de caja abierto.\n\n"
                "Abra la caja en el módulo Caja antes de cobrar.",
            )
            self._actualizar_boton_cobro()
            self._enfocar_lector()
            return

        if self.entry_pago_efectivo is not None:
            self._cobrar_desde_panel()
            return

        self._mostrar_dialogo_pago()

    def _mostrar_dialogo_pago(self):
        """
        Diálogo para ingresar pagos (efectivo, tarjeta, transferencia),
        calcular cambio y luego llamar a _guardar_y_imprimir_factura().
        """
        total = self._a_cobrar()
        es_cred_plazo = self._condicion_es_credito_plazo()

        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("Pago de factura")
        dlg.geometry("400x320" if es_cred_plazo else "380x260")
        dlg.resizable(False, False)

        ctk.CTkLabel(
            dlg,
            text=f"Total a pagar: RD$ {total:.2f}",
            font=("Arial", 14, "bold")
        ).pack(pady=(10, 4))

        if es_cred_plazo:
            ctk.CTkLabel(
                dlg,
                text=(
                    "Venta a crédito: puede dejar en RD$ 0,00 lo que no cobre ahora "
                    "y repartir el cobro entre efectivo / tarjeta / transferencia."
                ),
                font=("Arial", 11),
                text_color="#a3a3a3",
                wraplength=380,
                justify="left",
            ).pack(padx=12, pady=(0, 8))

        # ---- EFECTIVO ----
        frame_ef = ctk.CTkFrame(dlg)
        frame_ef.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_ef, text="Efectivo:").pack(side="left", padx=5)
        entry_ef = ctk.CTkEntry(frame_ef, width=120)
        entry_ef.pack(side="left", padx=5)
        if es_cred_plazo:
            entry_ef.insert(0, "0.00")
        else:
            entry_ef.insert(0, f"{total:.2f}")  # por defecto, todo en efectivo

        # ---- TARJETA ----
        frame_tar = ctk.CTkFrame(dlg)
        frame_tar.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_tar, text="Tarjeta:").pack(side="left", padx=5)
        entry_tar = ctk.CTkEntry(frame_tar, width=120)
        entry_tar.pack(side="left", padx=5)
        entry_tar.insert(0, "0.00")

        # ---- TRANSFERENCIA ----
        frame_tr = ctk.CTkFrame(dlg)
        frame_tr.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_tr, text="Transferencia:").pack(side="left", padx=5)
        entry_tr = ctk.CTkEntry(frame_tr, width=120)
        entry_tr.pack(side="left", padx=5)
        entry_tr.insert(0, "0.00")

        frame_auth = ctk.CTkFrame(dlg)
        frame_auth.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_auth, text="Autorización:").pack(side="left", padx=5)
        entry_auth = ctk.CTkEntry(frame_auth, width=140, placeholder_text="Voucher")
        entry_auth.pack(side="left", padx=5)

        # ---- CAMBIO / RESUMEN CRÉDITO ----
        lbl_cambio = ctk.CTkLabel(
            dlg,
            text="Cambio: RD$ 0.00",
            font=("Arial", 13, "bold")
        )
        lbl_cambio.pack(pady=(5, 5))

        def recalcular_cambio(*args):
            try:
                ef = _parse_monto_pago(entry_ef.get())
                tar = _parse_monto_pago(entry_tar.get())
                trf = _parse_monto_pago(entry_tr.get())
            except ValueError:
                if es_cred_plazo:
                    lbl_cambio.configure(
                        text="Cobrado ahora: RD$ 0.00  |  Pendiente: "
                        f"RD$ {total:.2f}"
                    )
                else:
                    lbl_cambio.configure(text="Cambio: RD$ 0.00")
                return

            if es_cred_plazo:
                cobrado = ef + tar + trf
                pend = max(0.0, total - cobrado)
                lbl_cambio.configure(
                    text=(
                        f"Cobrado ahora: RD$ {cobrado:.2f}  |  "
                        f"Pendiente: RD$ {pend:.2f}"
                    )
                )
                return

            otros = tar + trf
            por_pagar_con_efectivo = total - otros
            if por_pagar_con_efectivo < 0:
                por_pagar_con_efectivo = 0.0

            cambio = ef - por_pagar_con_efectivo
            if cambio < 0:
                cambio = 0.0

            lbl_cambio.configure(text=f"Cambio: RD$ {cambio:.2f}")

        entry_ef.bind("<KeyRelease>", lambda e: recalcular_cambio())
        entry_tar.bind("<KeyRelease>", lambda e: recalcular_cambio())
        entry_tr.bind("<KeyRelease>", lambda e: recalcular_cambio())

        # ---- BOTONES ----
        btn_frame = ctk.CTkFrame(dlg)
        btn_frame.pack(fill="x", padx=10, pady=15)

        def confirmar():
            try:
                ef = _parse_monto_pago(entry_ef.get())
                tar = _parse_monto_pago(entry_tar.get())
                trf = _parse_monto_pago(entry_tr.get())
            except ValueError:
                messagebox.showerror("Error", "Montos de pago inválidos.")
                return

            pagos = self._construir_pagos(ef, tar, trf, auth=entry_auth.get())
            if pagos is None:
                return

            dlg.destroy()
            self._guardar_y_imprimir_factura(
                pagos, imprimir=bool(self.var_imprimir_doc.get())
            )

        ctk.CTkButton(
            btn_frame,
            text="Confirmar pago",
            fg_color="#27ae60",
            command=confirmar
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Cancelar",
            fg_color="#c0392b",
            command=dlg.destroy
        ).pack(side="right", padx=5)

        recalcular_cambio()
        dlg.grab_set()
        dlg.focus_set()

    # ==========================
    #    IMPRESIÓN DEL TICKET
    # ==========================

    def _fila_ticket(self, izquierda: str, derecha: str, ancho: int) -> str:
        izquierda = str(izquierda)
        derecha = str(derecha)
        hueco = ancho - len(izquierda) - len(derecha)
        if hueco < 1:
            return f"{izquierda} {derecha}"
        return izquierda + (" " * hueco) + derecha

    def _fecha_ticket(self, valor: str) -> str:
        s = str(valor or "")
        if len(s) >= 16 and s[4] == "-" and s[7] == "-":
            return f"{s[8:10]}/{s[5:7]}/{s[0:4]} {s[11:16]}"
        return s

    def _es_credito_fiscal(self) -> bool:
        texto = (self.combo_tipo_comprobante.get() or "").lower() if self.combo_tipo_comprobante else ""
        return "crédito" in texto or "credito" in texto

    def _build_ticket_text(
        self,
        numero,
        fecha,
        usuario,
        detalles,
        subtotal_gravable,
        descuento_total,
        impuesto_total,
        total,
        precuenta: bool = False,
        pagos: list | None = None,
        propina: float = 0.0,
        cambio: float = 0.0,
    ):
        """Ticket térmico: pre-cuenta, factura de consumo o crédito fiscal."""
        ancho = self.db.get_ticket_width()
        lines = []

        def centro(texto: str) -> str:
            return str(texto)[:ancho].center(ancho)

        def corte() -> str:
            return "-" * ancho

        emp = self.db.get_empresa_info()
        lines.append(centro((emp.get("nombre") or "Mi empresa").strip() or "Mi empresa"))
        direccion = (emp.get("direccion") or "").replace("\r\n", "\n").replace("\r", "\n")
        for parte in direccion.split("\n"):
            resto = parte.strip()
            while resto:
                lines.append(centro(resto[:ancho]))
                resto = resto[ancho:].lstrip()
        telefono = (emp.get("telefono") or "").strip()
        if telefono:
            lines.append(centro(f"Tel. {telefono}"))
        rnc_emisor = (emp.get("rnc") or "").strip()
        if rnc_emisor:
            lines.append(centro(f"RNC {rnc_emisor}"))
        lines.append(corte())
        credito = self._es_credito_fiscal() and not precuenta
        titulo = "PRE-CUENTA" if precuenta else ("FACTURA PARA CREDITO FISCAL" if credito else "FACTURA")
        lines.append(centro(titulo))
        if precuenta:
            lines.append(centro("Documento no fiscal"))
        lines.append(corte())
        lines.append(self._fila_ticket("Fecha", self._fecha_ticket(fecha), ancho))
        if not precuenta and numero:
            lines.append(self._fila_ticket("Factura" if credito else "Ticket", str(numero), ancho))
        ncf = "" if precuenta else (getattr(self, "_ultimo_ncf", None) or "").strip()
        if ncf:
            lines.append(self._fila_ticket("NCF", ncf, ancho))
        cliente = ""
        if getattr(self, "entry_codigo_cliente", None) is not None:
            cliente = (self.entry_codigo_cliente.get() or "").strip()
        lines.append(self._fila_ticket("Cliente", cliente or "Consumidor final", ancho))
        documento = ""
        if getattr(self, "entry_rnc_cliente", None) is not None:
            documento = (self.entry_rnc_cliente.get() or "").strip()
        if credito and documento:
            lines.append(self._fila_ticket("RNC", documento, ancho))
        mesa = (getattr(self, "_conduce_mesa", "") or "").strip()
        if mesa:
            lines.append(self._fila_ticket("Mesa", mesa, ancho))
        pedido = (getattr(self, "_conduce_codigo", "") or "").strip()
        if pedido:
            lines.append(self._fila_ticket("Pedido", pedido, ancho))
        mesero = (getattr(self, "_conduce_usuario", "") or "").strip()
        if mesero:
            lines.append(self._fila_ticket("Mesero", mesero.split("@")[0], ancho))
        lines.append(corte())
        lines.append(self._fila_ticket("UDS DESCRIPCION", "IMPORTE", ancho))
        for desc, cant, pu, total_linea in detalles:
            nombre = str(desc)
            while nombre:
                lines.append(nombre[:ancho])
                nombre = nombre[ancho:]
            cant_txt = f"{float(cant):g}"
            lines.append(self._fila_ticket(f"  {cant_txt} x {float(pu):,.2f}", f"{float(total_linea):,.2f}", ancho))
        lines.append(corte())
        if descuento_total and descuento_total > 0:
            lines.append(self._fila_ticket("Descuento", f"{float(descuento_total):,.2f}", ancho))
        lines.append(self._fila_ticket("Sub Total", f"{float(subtotal_gravable):,.2f}", ancho))
        lines.append(self._fila_ticket("ITBIS 18%", f"{float(impuesto_total or 0):,.2f}", ancho))
        if propina and propina > 0:
            lines.append(self._fila_ticket("10% de ley", f"{float(propina):,.2f}", ancho))
        a_cobrar = round(float(total or 0) + float(propina or 0), 2)
        lines.append(self._fila_ticket("TOTAL", f"RD$ {a_cobrar:,.2f}", ancho))
        if credito:
            lines.append(corte())
            lines.append(self._fila_ticket("BASE 0%", "0.00", ancho))
            lines.append(self._fila_ticket("BASE 18%", f"{float(subtotal_gravable):,.2f}", ancho))
            lines.append(self._fila_ticket("ITBIS", f"{float(impuesto_total or 0):,.2f}", ancho))
        if not precuenta and pagos:
            lines.append(corte())
            for pago in pagos:
                lines.append(self._fila_ticket(str(pago.get("tipo") or "Pago"), f"{float(pago.get('monto') or 0):,.2f}", ancho))
                if pago.get("referencia"):
                    lines.append(f"Aut. {pago['referencia']}"[:ancho])
            lines.append(self._fila_ticket("Devuelta" if credito else "Cambio", f"{float(cambio or 0):,.2f}", ancho))
        if usuario and not precuenta:
            lines.append("")
            lines.append(centro(f"Le atendió: {str(usuario).upper()}"))
        lines.append(corte())
        lines.append(centro("GRACIAS POR SU VISITA"))
        lines.append("\n\n\n")
        return "\n".join(lines)

    def _imprimir_precuenta(self):
        if not self.factura_items:
            messagebox.showwarning("Pre-cuenta", "El ticket está vacío.")
            return
        self._recalcular_totales_y_refrescar()
        detalles = [
            (
                item["nombre"],
                item["cantidad"],
                item["precio"],
                float(item.get("subtotal_neto") or 0),
            )
            for item in self.factura_items
        ]
        descuento = self.descuentos_items_total + self.descuento_global_monto
        texto = self._build_ticket_text(
            "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            self.current_user,
            detalles,
            self.subtotal_total,
            descuento,
            self.impuestos_total,
            self.total_factura,
            precuenta=True,
            propina=self.propina_monto,
        )
        self._send_to_printer(texto)

    def _guardar_asiento(self, cursor, concepto: str, origen: str, origen_id: int, lineas: list) -> None:
        cursor.execute("SELECT id FROM asientos WHERE origen=? AND origen_id=?", (origen, int(origen_id)))
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO asientos (fecha, concepto, origen, origen_id, usuario) VALUES (?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), concepto[:180], origen, int(origen_id), self.current_user),
        )
        asiento_id = int(cursor.lastrowid)
        for codigo, debito, credito in lineas:
            cursor.execute("SELECT id FROM cuentas WHERE codigo=?", (codigo,))
            cuenta = cursor.fetchone()
            if not cuenta:
                raise ValueError(f"Falta la cuenta {codigo} en el libro")
            cursor.execute(
                "INSERT INTO asiento_lineas (asiento_id, cuenta_id, debito, credito) VALUES (?,?,?,?)",
                (asiento_id, cuenta[0], debito, credito),
            )

    def _send_to_printer(self, ticket_text: str):
        """
        ✅ NUEVO (Windows): imprime como RAW/ESC-POS usando la impresora instalada (driver).
        Esto evita Notepad y corrige:
          - texto partido/centrado raro
          - márgenes/escala
          - impresión tenue (activamos bold + double strike si soporta)
        """
        try:
            if not sys.platform.startswith("win"):
                # Linux / Mac fallback
                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".txt",
                    mode="w",
                    encoding="utf-8"
                ) as tmp:
                    tmp.write(ticket_text)
                    tmp_path = tmp.name

                messagebox.showinfo(
                    "Impresión",
                    f"Ticket generado:\n{tmp_path}\nImprimir manualmente."
                )
                return

            # --- Windows RAW ---
            try:
                import win32print
            except Exception:
                # fallback a Notepad si no está pywin32
                self._send_to_printer_notepad(ticket_text)
                return

            printer_name = None
            # Si en tu DB guardas un nombre, úsalo. Si no, usa default.
            try:
                printer_name = self.db.get_config("printer_name", None)
            except Exception:
                printer_name = None
            if printer_name is not None and str(printer_name).strip() == "":
                printer_name = None

            if not printer_name:
                printer_name = win32print.GetDefaultPrinter()

            ESC = b"\x1b"
            GS = b"\x1d"

            init = ESC + b"@"                 # initialize
            bold_on = ESC + b"E" + b"\x01"    # bold on
            bold_off = ESC + b"E" + b"\x00"   # bold off

            # Double-strike ON/OFF (no todas lo soportan, pero si lo soporta oscurece mucho)
            dbl_on = ESC + b"G" + b"\x01"
            dbl_off = ESC + b"G" + b"\x00"

            # Line spacing normal
            line_spacing = ESC + b"2"

            # Feed + (si tuviera cutter, intentará cortar; si no, lo ignora)
            feed = b"\n\n\n"
            cut = GS + b"V" + b"\x00"

            # IMPORTANTE: muchas térmicas se llevan mejor con cp437/cp850 que con utf-8
            data = ticket_text.encode("cp437", errors="replace")

            payload = init + line_spacing + bold_on + dbl_on + data + bold_off + dbl_off + feed + cut

            hPrinter = win32print.OpenPrinter(printer_name)
            try:
                hJob = win32print.StartDocPrinter(hPrinter, 1, ("Ticket", None, "RAW"))
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, payload)
                win32print.EndPagePrinter(hPrinter)
                win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)

        except Exception as e:
            # si falla RAW, intenta notepad
            try:
                self._send_to_printer_notepad(ticket_text)
            except Exception:
                messagebox.showerror("Error de impresión", f"No se pudo imprimir:\n{e}")

    def _send_to_printer_notepad(self, ticket_text: str):
        """
        Fallback antiguo (Notepad /p). No recomendado, pero útil si RAW falla.
        """
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".txt",
            mode="w",
            encoding="utf-8"
        ) as tmp:
            tmp.write(ticket_text)
            tmp_path = tmp.name

        subprocess.run(["notepad.exe", "/p", tmp_path], check=True)

    # ================================================
    #          GUARDAR FACTURA + GENERAR TICKET
    # ================================================

    def _guardar_presupuesto(self):
        """Cotización en BD sin movimiento de inventario ni cobros."""
        numero = f"P-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        conn = None
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            descuento_total = self.descuentos_items_total + self.descuento_global_monto

            lbl_comp = (self.combo_tipo_comprobante.get() or "").lower()
            if "gubernamental" in lbl_comp:
                tipo_comp_db = "gubernamental"
            elif "especial" in lbl_comp:
                tipo_comp_db = "especial"
            elif "crédito" in lbl_comp or "credito" in lbl_comp:
                tipo_comp_db = "credito_fiscal"
            else:
                tipo_comp_db = "consumidor_final"

            cursor.execute(
                """
                INSERT INTO facturas
                    (numero, tipo_comprobante, cliente_id, subtotal,
                     descuento_total, impuesto_total, total, estado, usuario, caja,
                     condicion_pago_id, fecha_vencimiento, moneda, tasa_cambio)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'cotizacion', ?, ?, ?, ?, 'DOP', 1.0)
                """,
                (
                    numero,
                    tipo_comp_db,
                    self._resolver_cliente_id(),
                    round(self.subtotal_total, 2),
                    round(descuento_total, 2),
                    round(self.impuestos_total, 2),
                    round(self.total_factura, 2),
                    self.current_user,
                    None,
                    self._condicion_pago_id_actual(),
                    self._fecha_vencimiento_iso(),
                ),
            )
            factura_id = cursor.lastrowid

            for item in self.factura_items:
                cursor.execute(
                    """
                    INSERT INTO factura_detalle
                        (factura_id, producto_id, descripcion, cantidad,
                         precio_unitario, descuento_item, impuesto_item, total_linea)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        factura_id,
                        item["id"],
                        item["nombre"],
                        item["cantidad"],
                        item["precio"],
                        round(item["descuento"], 2),
                        round(float(item.get("impuesto_item") or 0), 2),
                        round(float(item.get("total_linea") or item["subtotal_neto"]), 2),
                    ),
                )

            conn.commit()
            conn.close()

            messagebox.showinfo(
                "Presupuesto",
                f"Presupuesto {numero} guardado. Confirme la venta desde el listado "
                "(Pagar documento) cuando el cliente cierre.",
            )
            self.factura_items.clear()
            self._cliente_id = None
            self.var_solo_presupuesto.set(False)
            self._recalcular_totales_y_refrescar()
            self._actualizar_vista_secuencia_documento(regenerar=True)
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            messagebox.showerror("Error", f"No se pudo guardar el presupuesto:\n{e}")

    def _numero_factura_libre(self, cursor) -> str:
        base = self._generar_numero_factura()
        numero = base
        n = 2
        while True:
            cursor.execute("SELECT 1 FROM facturas WHERE numero = ?", (numero,))
            if cursor.fetchone() is None:
                return numero
            numero = f"{base}-{n}"
            n += 1

    def _guardar_y_imprimir_factura(self, pagos, imprimir: bool = True):
        numero = None

        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            descuento_total = (
                self.descuentos_items_total + self.descuento_global_monto + self.puntos_rebaja
            )

            lbl_comp = (self.combo_tipo_comprobante.get() or "").lower()
            if "gubernamental" in lbl_comp:
                tipo_comp_db = "gubernamental"
            elif "especial" in lbl_comp:
                tipo_comp_db = "especial"
            elif "crédito" in lbl_comp or "credito" in lbl_comp:
                tipo_comp_db = "credito_fiscal"
            else:
                tipo_comp_db = "consumidor_final"

            # Guardar encabezado
            caja_abierta = self.db.fetch_caja_abierta_row()
            cierre_id = int(caja_abierta[0]) if caja_abierta else None
            encf = None
            if (self.db.get_config("ecf_activo", "0") or "0") == "1":
                from ecf import dgii as _dgii
                tipo_electronico = _dgii.tipo_ecf_de(tipo_comp_db)
                if tipo_electronico:
                    reserva = self.db.tomar_siguiente_encf(tipo_electronico, conn)
                    if reserva is None:
                        raise ValueError(
                            f"No hay e-NCF disponibles del tipo {tipo_electronico}."
                        )
                    encf = reserva[0]
            ncf = encf or self.db.tomar_siguiente_ncf(tipo_comp_db, conn=conn)
            self._ultimo_ncf = ncf
            lista = self.combo_lista.get() if self.combo_lista is not None else "Público"
            numero = self._numero_factura_libre(cursor)
            cursor.execute(
                """
                INSERT INTO facturas
                    (numero, tipo_comprobante, cliente_id, subtotal,
                     descuento_total, impuesto_total, total, estado, usuario, caja,
                     condicion_pago_id, fecha_vencimiento, moneda, tasa_cambio,
                     cierre_id, ncf, lista_precio, encf, propina)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DOP', 1.0, ?, ?, ?, ?, ?)
                """,
                (
                    numero,
                    tipo_comp_db,
                    self._resolver_cliente_id(),
                    round(self.subtotal_total, 2),
                    round(descuento_total, 2),
                    round(self.impuestos_total, 2),
                    round(self.total_factura, 2),
                    "emitida",
                    self.current_user,
                    None,
                    self._condicion_pago_id_actual(),
                    self._fecha_vencimiento_iso(),
                    cierre_id,
                    ncf,
                    lista,
                    encf,
                    round(self.propina_monto, 2),
                )
            )

            factura_id = cursor.lastrowid

            # Guardar detalle y actualizar inventario
            detalles_ticket = []
            low_stock = []

            for item in self.factura_items:
                cursor.execute(
                    """
                    INSERT INTO factura_detalle
                        (factura_id, producto_id, descripcion, cantidad,
                         precio_unitario, descuento_item, impuesto_item, total_linea)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        factura_id,
                        item["id"],
                        item["nombre"],
                        item["cantidad"],
                        item["precio"],
                        round(item["descuento"], 2),
                        round(float(item.get("impuesto_item") or 0), 2),
                        round(float(item.get("total_linea") or item["subtotal_neto"]), 2),
                    )
                )

                componentes = item.get("componentes") or []
                if componentes:
                    for comp in componentes:
                        cpid = int(comp["id"])
                        cqty = float(comp["cantidad"]) * float(item["cantidad"])
                        cursor.execute(
                            "UPDATE productos SET stock = stock - ? WHERE id = ?",
                            (cqty, cpid),
                        )
                        cursor.execute(
                            """
                            SELECT IFNULL(NULLIF(TRIM(bodega_codigo), ''), '')
                            FROM productos WHERE id = ?
                            """,
                            (cpid,),
                        )
                        bod_row = cursor.fetchone()
                        bod_codigo = (bod_row[0] or "").strip() or None
                        self.db.ajustar_stock_bodega(cpid, -cqty, bod_codigo, conn=conn)
                        self.db.insert_movimiento_kardex(
                            cpid,
                            "venta",
                            -cqty,
                            ajustar_stock=False,
                            referencia=numero,
                            factura_id=factura_id,
                            usuario=self.current_user,
                            tipo_codigo="FA",
                            entidad_nombre="Cliente consumidor final",
                            bodega_codigo=bod_codigo,
                            precio_unitario=float(item["precio"]),
                            descripcion_mov=f"Venta combo: Factura {numero}",
                            conn=conn,
                        )
                        cursor.execute(
                            "SELECT stock, IFNULL(stock_minimo, 0) FROM productos WHERE id = ?",
                            (cpid,),
                        )
                        stk, minimo = cursor.fetchone()
                        if stk <= minimo:
                            low_stock.append((item["nombre"], stk))
                elif item.get("id"):
                    cursor.execute(
                        "UPDATE productos SET stock = stock - ? WHERE id = ?",
                        (item["cantidad"], item["id"])
                    )

                    cursor.execute(
                        """
                        SELECT IFNULL(NULLIF(TRIM(bodega_codigo), ''), '')
                        FROM productos WHERE id = ?
                        """,
                        (item["id"],),
                    )
                    bod_row = cursor.fetchone()
                    bod_codigo = (bod_row[0] or "").strip() or None
                    self.db.ajustar_stock_bodega(
                        item["id"], -float(item["cantidad"]), bod_codigo, conn=conn
                    )

                    self.db.insert_movimiento_kardex(
                        item["id"],
                        "venta",
                        -float(item["cantidad"]),
                        ajustar_stock=False,
                        referencia=numero,
                        factura_id=factura_id,
                        usuario=self.current_user,
                        tipo_codigo="FA",
                        entidad_nombre="Cliente consumidor final",
                        bodega_codigo=bod_codigo,
                        precio_unitario=float(item["precio"]),
                        descripcion_mov=f"Venta: Factura {numero}",
                        conn=conn,
                    )

                    cursor.execute(
                        "SELECT stock, IFNULL(stock_minimo, 0) FROM productos WHERE id = ?",
                        (item["id"],),
                    )
                    stk, minimo = cursor.fetchone()
                    if stk <= minimo:
                        low_stock.append((item["nombre"], stk))

                # agregar al ticket
                detalles_ticket.append(
                    (
                        item["nombre"],
                        item["cantidad"],
                        item["precio"],
                        float(item.get("subtotal_neto") or 0),
                    )
                )

            ahora_pago = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for p in pagos:
                cursor.execute(
                    """
                    INSERT INTO pagos_factura
                        (factura_id, tipo_pago, monto, referencia, cierre_id, fecha, usuario, es_abono)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        factura_id,
                        p["tipo"],
                        round(p["monto"], 2),
                        p.get("referencia") or None,
                        cierre_id,
                        ahora_pago,
                        self.current_user,
                    ),
                )

            cursor.execute(
                "SELECT codigo FROM metodos_pago WHERE IFNULL(activo,1)=1 AND IFNULL(afecta_caja,0)=1"
            )
            afectan = {fila[0] for fila in cursor.fetchall()} or {"efectivo"}
            caja = round(sum(p["monto"] for p in pagos if p["tipo"] in afectan), 2)
            saldo = round(sum(p["monto"] for p in pagos if p["tipo"] == "saldo_favor"), 2)
            banco = round(sum(p["monto"] for p in pagos if p["tipo"] not in afectan and p["tipo"] != "saldo_favor"), 2)
            fiado = round(max(0.0, self._a_cobrar() - caja - banco - saldo), 2)
            self._guardar_asiento(
                cursor,
                f"Venta {numero}",
                "factura",
                factura_id,
                asiento_venta(
                    caja=caja,
                    banco=banco,
                    saldo=saldo,
                    fiado=fiado,
                    ventas=round(self.total_factura - self.impuestos_total, 2),
                    itbis=round(self.impuestos_total, 2),
                    propina=round(self.propina_monto, 2),
                ),
            )
            cursor.execute("SELECT cliente_id FROM facturas WHERE id=?", (factura_id,))
            cliente_id = cursor.fetchone()[0]
            if cliente_id:
                cursor.execute(
                    "UPDATE clientes SET puntos = MAX(0, IFNULL(puntos,0) - ? + ?) WHERE id = ?",
                    (self.puntos_usados, puntos_ganados(self.total_factura), cliente_id),
                )
            saldo_usado = round(sum(p["monto"] for p in pagos if p["tipo"] == "saldo_favor"), 2)
            if saldo_usado > 0 and cliente_id:
                cursor.execute(
                    """
                    INSERT INTO saldos_cliente (cliente_id, monto, concepto, factura_id, usuario)
                    VALUES (?,?,?,?,?)
                    """,
                    (cliente_id, -saldo_usado, f"Usado en {numero}", factura_id, self.current_user),
                )
            if self._conduce_id:
                marcar_conduce_facturado(cursor, int(self._conduce_id), int(factura_id))

            # Fecha real guardada
            cursor.execute("SELECT fecha FROM facturas WHERE id = ?", (factura_id,))
            row_fecha = cursor.fetchone()
            fecha_txt = row_fecha[0] if row_fecha else datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            conn.commit()
            conn.close()

            ecf_aviso = None
            if encf:
                try:
                    from ecf import servicio as ecf_srv
                    reg = ecf_srv.emitir_factura(self.db, factura_id) or {}
                    if reg.get("id"):
                        ecf_srv.enviar_en_segundo_plano(lambda: self.db, reg["id"])
                    if reg.get("estado") == "error":
                        ecf_aviso = reg.get("mensaje") or "No se pudo firmar el e-CF"
                except Exception as exc:
                    ecf_aviso = str(exc)

            # Avisos
            if low_stock:
                msg = "Productos con stock bajo:\n\n"
                for n, s in low_stock:
                    msg += f"• {n} → {s}\n"
                messagebox.showwarning("Stock bajo", msg)
            if ecf_aviso:
                messagebox.showwarning(
                    "e-CF",
                    "La factura quedó guardada. El comprobante electrónico no se firmó:\n"
                    + ecf_aviso,
                )

            # -----------------------------
            # GENERAR TICKET DINÁMICO
            # -----------------------------
            ticket_text = self._build_ticket_text(
                numero,
                fecha_txt,
                self.current_user,
                detalles_ticket,
                self.subtotal_total,
                descuento_total,
                self.impuestos_total,
                self.total_factura,
                pagos=pagos,
                propina=self.propina_monto,
                cambio=self._cambio_ticket,
            )

            # Guardar ticket en carpeta 'facturas'
            try:
                from app_paths import data_directory, is_frozen

                base_dir = (
                    data_directory()
                    if is_frozen()
                    else os.path.dirname(os.path.abspath(__file__))
                )
                facturas_dir = os.path.join(base_dir, "facturas")
                os.makedirs(facturas_dir, exist_ok=True)

                ticket_path = os.path.join(facturas_dir, f"{numero}.txt")
                with open(ticket_path, "w", encoding="utf-8") as f:
                    f.write(ticket_text)
            except Exception as e:
                print("Error guardando ticket en carpeta 'facturas':", e)

            # Enviar a la impresora si el usuario lo solicitó
            if imprimir:
                self._send_to_printer(ticket_text)
            else:
                messagebox.showinfo(
                    "Factura guardada",
                    f"Factura {numero} guardada. No se envió a imprimir.",
                )

            # limpiar la factura en pantalla
            self.factura_items.clear()
            self._cliente_id = None
            self._recalcular_totales_y_refrescar()
            self._actualizar_vista_secuencia_documento(regenerar=True)
            self._refrescar_cola_conduces()

        except Exception as e:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
            messagebox.showerror("Error", f"No se pudo guardar/imprimir la factura:\n{e}")
