# reporte_ventas_manager.py

import customtkinter as ctk
from tkinter import ttk, messagebox
from database import Database


class ReporteVentasManager:
    def __init__(self, parent):
        self.parent = parent
        self.db = Database()

        # Widgets
        self.main_frame = None
        self.tree_facturas = None
        self.tree_detalle = None
        self.entry_fecha_desde = None
        self.entry_fecha_hasta = None
        self.entry_usuario = None

        self._setup_ui()
        self.load_facturas()

    # ==========================================
    #                  UI
    # ==========================================

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Título
        title = ctk.CTkLabel(
            self.main_frame,
            text="📊 REPORTE DE VENTAS",
            font=("Arial", 20, "bold"),
            text_color="white"
        )
        title.pack(pady=(0, 10))

        # -----------------------------
        #   FILTROS (fechas / usuario)
        # -----------------------------
        filtros_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        filtros_frame.pack(fill="x", padx=5, pady=(0, 10))

        # Fecha desde
        lbl_desde = ctk.CTkLabel(
            filtros_frame,
            text="Fecha desde (YYYY-MM-DD):",
            font=("Arial", 12)
        )
        lbl_desde.grid(row=0, column=0, padx=8, pady=5, sticky="w")
        self.entry_fecha_desde = ctk.CTkEntry(
            filtros_frame,
            width=140,
            placeholder_text="ej: 2025-11-01"
        )
        self.entry_fecha_desde.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        # Fecha hasta
        lbl_hasta = ctk.CTkLabel(
            filtros_frame,
            text="Fecha hasta (YYYY-MM-DD):",
            font=("Arial", 12)
        )
        lbl_hasta.grid(row=0, column=2, padx=8, pady=5, sticky="w")
        self.entry_fecha_hasta = ctk.CTkEntry(
            filtros_frame,
            width=140,
            placeholder_text="ej: 2025-11-30"
        )
        self.entry_fecha_hasta.grid(row=0, column=3, padx=5, pady=5, sticky="w")

        # Usuario
        lbl_usuario = ctk.CTkLabel(
            filtros_frame,
            text="Usuario:",
            font=("Arial", 12)
        )
        lbl_usuario.grid(row=0, column=4, padx=8, pady=5, sticky="w")
        self.entry_usuario = ctk.CTkEntry(
            filtros_frame,
            width=120,
            placeholder_text="admin / empleado"
        )
        self.entry_usuario.grid(row=0, column=5, padx=5, pady=5, sticky="w")

        # Botones filtro
        btn_filtrar = ctk.CTkButton(
            filtros_frame,
            text="Filtrar",
            width=90,
            command=self.aplicar_filtros
        )
        btn_filtrar.grid(row=0, column=6, padx=8, pady=5)

        btn_limpiar = ctk.CTkButton(
            filtros_frame,
            text="Limpiar",
            width=90,
            fg_color="#A33",
            command=self.limpiar_filtros
        )
        btn_limpiar.grid(row=0, column=7, padx=8, pady=5)

        # -----------------------------
        #   TABLA DE FACTURAS
        # -----------------------------
        facturas_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        facturas_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        lbl_facturas = ctk.CTkLabel(
            facturas_frame,
            text="Listado de Facturas (Ventas)",
            font=("Arial", 14, "bold"),
            text_color="white"
        )
        lbl_facturas.pack(anchor="w", padx=5, pady=(0, 5))

        tree_container = ctk.CTkFrame(facturas_frame, fg_color="#2B2B2B")
        tree_container.pack(fill="both", expand=True)

        # ⚠️ Ahora incluimos descuento_total
        columns = (
            "id", "numero", "fecha", "usuario",
            "tipo_comprobante", "formas_pago",
            "subtotal", "descuento", "impuesto",
            "total", "estado"
        )

        self.tree_facturas = ttk.Treeview(
            tree_container,
            columns=columns,
            show="headings",
            height=7
        )

        # Estilos de Treeview
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
            ("No. Factura", 120),
            ("Fecha", 150),
            ("Usuario", 100),
            ("Tipo", 120),
            ("Pago", 120),
            ("Subtotal", 90),
            ("Desc.", 90),
            ("ITBIS", 90),
            ("Total", 90),
            ("Estado", 80),
        ]

        for (col, (text, width)) in zip(columns, headers):
            self.tree_facturas.heading(col, text=text)
            self.tree_facturas.column(col, width=width, anchor="center")

        # Scrollbar
        scroll_y = ttk.Scrollbar(
            tree_container,
            orient="vertical",
            command=self.tree_facturas.yview
        )
        self.tree_facturas.configure(yscrollcommand=scroll_y.set)

        self.tree_facturas.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        # Evento de selección para cargar detalle
        self.tree_facturas.bind("<<TreeviewSelect>>", self.on_factura_selected)

        # -----------------------------
        #   DETALLE DE FACTURA
        # -----------------------------
        detalle_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        detalle_frame.pack(fill="both", expand=True, padx=5, pady=(5, 0))

        lbl_detalle = ctk.CTkLabel(
            detalle_frame,
            text="Detalle de la factura seleccionada",
            font=("Arial", 14, "bold"),
            text_color="white"
        )
        lbl_detalle.pack(anchor="w", padx=5, pady=(0, 5))

        detalle_container = ctk.CTkFrame(detalle_frame, fg_color="#2B2B2B")
        detalle_container.pack(fill="both", expand=True)

        detalle_cols = ("descripcion", "cantidad", "precio", "itbis", "total_linea")

        self.tree_detalle = ttk.Treeview(
            detalle_container,
            columns=detalle_cols,
            show="headings",
            height=5
        )

        detalle_headers = [
            ("Producto / Descripción", 260),
            ("Cantidad", 80),
            ("Precio", 90),
            ("ITBIS", 90),
            ("Total línea", 100),
        ]

        for (col, (text, width)) in zip(detalle_cols, detalle_headers):
            self.tree_detalle.heading(col, text=text)
            self.tree_detalle.column(col, width=width, anchor="center")

        scroll_y_det = ttk.Scrollbar(
            detalle_container,
            orient="vertical",
            command=self.tree_detalle.yview
        )
        self.tree_detalle.configure(yscrollcommand=scroll_y_det.set)

        self.tree_detalle.pack(side="left", fill="both", expand=True)
        scroll_y_det.pack(side="right", fill="y")

    # ==========================================
    #          CARGAR / FILTRAR FACTURAS
    # ==========================================

    def limpiar_tabla_facturas(self):
        for item in self.tree_facturas.get_children():
            self.tree_facturas.delete(item)

    def limpiar_tabla_detalle(self):
        for item in self.tree_detalle.get_children():
            self.tree_detalle.delete(item)

    def aplicar_filtros(self):
        self.load_facturas()

    def limpiar_filtros(self):
        self.entry_fecha_desde.delete(0, "end")
        self.entry_fecha_hasta.delete(0, "end")
        self.entry_usuario.delete(0, "end")
        self.load_facturas()

    def load_facturas(self):
        self.limpiar_tabla_facturas()
        self.limpiar_tabla_detalle()

        fecha_desde = self.entry_fecha_desde.get().strip()
        fecha_hasta = self.entry_fecha_hasta.get().strip()
        usuario = self.entry_usuario.get().strip()

        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Incluimos descuento_total y formas de pago desde pagos_factura
        query = """
            SELECT 
                f.id,
                f.numero,
                f.fecha,
                f.usuario,
                f.tipo_comprobante,
                IFNULL(GROUP_CONCAT(DISTINCT p.tipo_pago), 'N/A') AS formas_pago,
                f.subtotal,
                f.descuento_total,
                f.impuesto_total,
                f.total,
                f.estado
            FROM facturas f
            LEFT JOIN pagos_factura p ON p.factura_id = f.id
            WHERE 1=1
        """
        params = []

        # Filtro fechas (asumiendo f.fecha es texto tipo 'YYYY-MM-DD HH:MM:SS')
        if fecha_desde:
            query += " AND date(f.fecha) >= date(?)"
            params.append(fecha_desde)

        if fecha_hasta:
            query += " AND date(f.fecha) <= date(?)"
            params.append(fecha_hasta)

        # Filtro usuario
        if usuario:
            query += " AND f.usuario LIKE ?"
            params.append(f"%{usuario}%")

        query += """
            GROUP BY 
                f.id, f.numero, f.fecha, f.usuario, f.tipo_comprobante,
                f.subtotal, f.descuento_total, f.impuesto_total, f.total, f.estado
            ORDER BY f.fecha DESC
        """

        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        except Exception as e:
            conn.close()
            messagebox.showerror("Error", f"No se pudieron cargar las ventas:\n{e}")
            return

        conn.close()

        for row in rows:
            (
                fid,
                numero,
                fecha,
                usuario,
                tipo,
                formas_pago,
                subtotal,
                descuento,
                impuesto,
                total,
                estado
            ) = row

            subtotal = float(subtotal or 0)
            descuento = float(descuento or 0)
            impuesto = float(impuesto or 0)
            total = float(total or 0)

            self.tree_facturas.insert(
                "",
                "end",
                values=(
                    fid,
                    numero,
                    fecha,
                    usuario,
                    tipo,
                    formas_pago,
                    f"RD$ {subtotal:.2f}",
                    f"RD$ {descuento:.2f}",
                    f"RD$ {impuesto:.2f}",
                    f"RD$ {total:.2f}",
                    estado
                )
            )

    # ==========================================
    #        DETALLE DE FACTURA SELECCIONADA
    # ==========================================

    def on_factura_selected(self, event):
        selected = self.tree_facturas.selection()
        if not selected:
            return

        item = self.tree_facturas.item(selected[0])
        factura_id = item["values"][0]
        self.load_detalle_factura(factura_id)

    def load_detalle_factura(self, factura_id: int):
        self.limpiar_tabla_detalle()

        conn = self.db.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT descripcion, cantidad, precio_unitario, impuesto_item, total_linea
                FROM factura_detalle
                WHERE factura_id = ?
                """,
                (factura_id,)
            )
            rows = cursor.fetchall()
        except Exception as e:
            conn.close()
            messagebox.showerror(
                "Error",
                f"No se pudo cargar el detalle de la factura:\n{e}"
            )
            return

        conn.close()

        for desc, cant, precio, itbis, total_linea in rows:
            self.tree_detalle.insert(
                "",
                "end",
                values=(
                    desc,
                    f"{float(cant):.2f}",
                    f"RD$ {float(precio):.2f}",
                    f"RD$ {float(itbis or 0):.2f}",
                    f"RD$ {float(total_linea or 0):.2f}",
                )
            )
