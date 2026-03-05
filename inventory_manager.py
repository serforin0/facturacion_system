import customtkinter as ctk
from tkinter import ttk, messagebox
import tkinter as tk
from PIL import Image, ImageTk
import os

from database import Database
from styles import Styles
from image_manager import ImageManager
from modern_image_selector import ModernImageSelector


class InventoryManager:
    def __init__(self, parent_frame):
        self.db = Database()
        self.image_manager = ImageManager()
        self.parent = parent_frame

        # variables para filtros
        self.search_entry = None
        self.category_filter = None

        # para edición
        self.edit_image_path = None

        # Paginación
        self.current_page = 1
        self.limit_per_page = 50
        self.total_pages = 1

        self.setup_ui()
        self.load_products()

    # =====================================================
    #                       UI
    # =====================================================

    def setup_ui(self):
        # Frame principal (pegado arriba para ganar alto)
        self.main_frame = ctk.CTkFrame(self.parent, **Styles.get_frame_style())
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        # Frame de controles (agregar producto)
        self.setup_controls_frame()

        # Frame de lista de productos
        self.setup_products_frame()

    def setup_controls_frame(self):
        controls_frame = ctk.CTkFrame(self.main_frame, **Styles.get_frame_style())
        controls_frame.pack(fill="x", padx=10, pady=(3, 3))

        ctk.CTkLabel(
            controls_frame,
            text="Nuevo producto",
            font=("Arial", 14, "bold")
        ).pack(pady=(3, 0), anchor="w", padx=10)

        main_form_frame = ctk.CTkFrame(controls_frame)
        main_form_frame.pack(fill="x", padx=10, pady=(3, 3))

        # ================================
        #   COLUMNA IZQUIERDA - TEXTO
        # ================================
        left_frame = ctk.CTkFrame(main_form_frame, fg_color="transparent")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        form_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        form_frame.pack(fill="x")

        # Panel 1: Nombre / Descripción / Código barras
        col1 = ctk.CTkFrame(form_frame, fg_color="transparent")
        col1.pack(side="left", fill="x", expand=True, padx=2)

        ctk.CTkLabel(col1, text="Nombre:", font=("Arial", 11)).pack(anchor="w")
        self.name_entry = ctk.CTkEntry(col1, placeholder_text="Ej: Corona Extra", height=26)
        self.name_entry.pack(fill="x", pady=1)

        ctk.CTkLabel(col1, text="Descripción:", font=("Arial", 11)).pack(anchor="w")
        self.desc_entry = ctk.CTkEntry(col1, placeholder_text="Ej: Cerveza clara 355ml", height=26)
        self.desc_entry.pack(fill="x", pady=1)

        ctk.CTkLabel(col1, text="Código de Barras:", font=("Arial", 11)).pack(anchor="w")
        self.barcode_entry = ctk.CTkEntry(
            col1,
            placeholder_text="Escanea aquí o escribe el código",
            height=26
        )
        self.barcode_entry.pack(fill="x", pady=1)

        # Panel 2: Precios
        col2 = ctk.CTkFrame(form_frame, fg_color="transparent")
        col2.pack(side="left", fill="x", expand=True, padx=2)

        ctk.CTkLabel(col2, text="Precio Inicial ($):", font=("Arial", 11)).pack(anchor="w")
        self.base_price_entry = ctk.CTkEntry(
            col2,
            placeholder_text="Ej: 30.00",
            height=26
        )
        self.base_price_entry.pack(fill="x", pady=1)

        ctk.CTkLabel(col2, text="Precio Venta ($):", font=("Arial", 11)).pack(anchor="w")
        self.price_entry = ctk.CTkEntry(
            col2,
            placeholder_text="Ej: 25.00",
            height=26
        )
        self.price_entry.pack(fill="x", pady=1)

        ctk.CTkLabel(col2, text="Precio Mínimo ($):", font=("Arial", 11)).pack(anchor="w")
        self.min_price_entry = ctk.CTkEntry(
            col2,
            placeholder_text="Ej: 20.00",
            height=26
        )
        self.min_price_entry.pack(fill="x", pady=1)

        # Panel 3: Categoría / Stock
        col3 = ctk.CTkFrame(form_frame, fg_color="transparent")
        col3.pack(side="left", fill="x", expand=True, padx=2)

        ctk.CTkLabel(col3, text="Categoría:", font=("Arial", 11)).pack(anchor="w")
        self.category_combo = ctk.CTkComboBox(
            col3,
            values=self.get_categories(),
            height=26
        )
        self.category_combo.pack(fill="x", pady=1)

        ctk.CTkLabel(col3, text="Stock Inicial:", font=("Arial", 11)).pack(anchor="w")
        self.stock_entry = ctk.CTkEntry(
            col3,
            placeholder_text="Ej: 50",
            height=26
        )
        self.stock_entry.pack(fill="x", pady=1)

        ctk.CTkLabel(col3, text="Stock Mínimo:", font=("Arial", 11)).pack(anchor="w")
        self.min_stock_entry = ctk.CTkEntry(
            col3,
            placeholder_text="Ej: 5",
            height=26
        )
        self.min_stock_entry.pack(fill="x", pady=1)

        # ================================
        #   COLUMNA DERECHA - IMAGEN
        # ================================
        right_frame = ctk.CTkFrame(main_form_frame, width=190, height=150)
        right_frame.pack(side="right", fill="y", padx=(5, 0))
        right_frame.pack_propagate(False)

        ctk.CTkLabel(
            right_frame,
            text="Imagen",
            font=("Arial", 12, "bold")
        ).pack(pady=(3, 2))

        self.image_selector = ModernImageSelector(right_frame, self.image_manager)
        self.image_selector.pack(padx=3, pady=3, fill="both", expand=True)

        # ================================
        #   BOTONES CRUD
        # ================================
        button_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
        button_frame.pack(fill="x", padx=10, pady=(0, 3))

        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)

        ctk.CTkButton(
            button_frame,
            text="➕ AGREGAR",
            command=self.add_product,
            fg_color=Styles.SECONDARY,
            height=30,
            font=("Arial", 11, "bold")
        ).grid(row=0, column=0, padx=4, pady=2, sticky="ew")

        ctk.CTkButton(
            button_frame,
            text="🔄 RECARGAR",
            command=lambda: self.load_products(),
            fg_color=Styles.PRIMARY,
            height=30,
            font=("Arial", 11, "bold")
        ).grid(row=0, column=1, padx=4, pady=2, sticky="ew")

        ctk.CTkButton(
            button_frame,
            text="✏️ EDITAR",
            command=self.edit_product,
            fg_color="#FFA500",
            height=30,
            font=("Arial", 11, "bold")
        ).grid(row=0, column=2, padx=4, pady=2, sticky="ew")

        ctk.CTkButton(
            button_frame,
            text="🗑️ ELIMINAR",
            command=self.delete_product,
            fg_color=Styles.DANGER,
            height=30,
            font=("Arial", 11, "bold")
        ).grid(row=0, column=3, padx=4, pady=2, sticky="ew")

    def setup_products_frame(self):
        products_frame = ctk.CTkFrame(self.main_frame, **Styles.get_frame_style())
        products_frame.pack(fill="both", expand=True, padx=10, pady=(3, 3))

        search_frame = ctk.CTkFrame(products_frame, fg_color="transparent")
        search_frame.pack(fill="x", padx=10, pady=(3, 3))

        ctk.CTkLabel(search_frame, text="Buscar:", font=("Arial", 11)).pack(
            side="left", padx=(0, 5)
        )

        self.search_entry = ctk.CTkEntry(
            search_frame,
            placeholder_text="Nombre, descripción o código...",
            height=26
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.search_entry.bind("<Return>", lambda e: self.search_products())

        ctk.CTkLabel(search_frame, text="Categoría:", font=("Arial", 11)).pack(
            side="left", padx=(8, 5)
        )

        self.category_filter = ctk.CTkComboBox(
            search_frame,
            values=["Todas"] + self.get_categories(),
            width=140,
            height=26
        )
        self.category_filter.set("Todas")
        self.category_filter.pack(side="left")

        ctk.CTkButton(
            search_frame,
            text="🔍",
            width=40,
            height=26,
            command=self.search_products
        ).pack(side="left", padx=(6, 3))

        ctk.CTkButton(
            search_frame,
            text="✖",
            width=40,
            height=26,
            fg_color=Styles.DANGER,
            command=self.clear_search
        ).pack(side="left")

        tree_frame = ctk.CTkFrame(products_frame)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 3))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=24,
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
        style.map("Treeview", background=[("selected", "#22559b")])

        self.tree = ttk.Treeview(
            tree_frame,
            columns=(
                "ID", "Nombre", "Descripción", "Precio", "Stock",
                "Categoría", "Stock Mín", "Código"
            ),
            show="headings",
            height=100
        )

        columns = [
            ("ID", 40),
            ("Nombre", 150),
            ("Descripción", 230),
            ("Precio", 90),
            ("Stock", 70),
            ("Categoría", 110),
            ("Stock Mín", 80),
            ("Código", 130),
        ]

        for col, width in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="center")

        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda e: self.edit_product())

        report_frame = ctk.CTkFrame(products_frame, fg_color="transparent")
        report_frame.pack(fill="x", padx=10, pady=(0, 3))

        # Controles de Paginación
        pag_frame = ctk.CTkFrame(report_frame, fg_color="transparent")
        pag_frame.pack(side="left", fill="y", pady=2)

        self.btn_prev = ctk.CTkButton(
            pag_frame, text="< Anterior", width=80, height=26,
            command=self.prev_page
        )
        self.btn_prev.pack(side="left", padx=2)

        self.lbl_page = ctk.CTkLabel(pag_frame, text="Página 1 de 1", font=("Arial", 11, "bold"))
        self.lbl_page.pack(side="left", padx=10)

        self.btn_next = ctk.CTkButton(
            pag_frame, text="Siguiente >", width=80, height=26,
            command=self.next_page
        )
        self.btn_next.pack(side="left", padx=2)

        ctk.CTkButton(
            report_frame,
            text="📊 Stock bajo",
            command=self.low_stock_report,
            fg_color=Styles.WARNING,
            height=28,
            font=("Arial", 11, "bold"),
            width=140
        ).pack(side="right", pady=2)

    # =====================================================
    #                    LÓGICA DE DATOS
    # =====================================================

    def get_categories(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT nombre FROM categorias ORDER BY nombre")
        categories = [row[0] for row in cursor.fetchall()]
        conn.close()
        return categories

    # ---------- BÚSQUEDA / FILTRO -----------

    def clear_search(self):
        if self.search_entry:
            self.search_entry.delete(0, "end")
        if self.category_filter:
            self.category_filter.set("Todas")
        self.current_page = 1
        self.load_products()

    # ---------- RECARGAR CON BUSQUEDA PÁGINA 1 ----------
    def search_products(self):
        self.current_page = 1
        texto = self.search_entry.get().strip() if self.search_entry else ""
        categoria = self.category_filter.get() if self.category_filter else "Todas"
        self.load_products(search_text=texto, category_filter=categoria)

    # ---------- CRUD -----------

    def add_product(self):
        if not self.validate_fields():
            return

        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(productos)")
            columns = [column[1] for column in cursor.fetchall()]

            if "precio_base" not in columns:
                cursor.execute("ALTER TABLE productos ADD COLUMN precio_base REAL")
            if "precio_minimo" not in columns:
                cursor.execute("ALTER TABLE productos ADD COLUMN precio_minimo REAL")

            cursor.execute(
                "SELECT id FROM categorias WHERE nombre = ?",
                (self.category_combo.get(),)
            )
            categoria_row = cursor.fetchone()
            if not categoria_row:
                messagebox.showerror("Error", "Selecciona una categoría válida.")
                return
            categoria_id = categoria_row[0]

            precio_venta = float(self.price_entry.get())
            precio_base = float(self.base_price_entry.get()
                                ) if self.base_price_entry.get().strip() else precio_venta
            precio_minimo = float(self.min_price_entry.get()
                                  ) if self.min_price_entry.get().strip() else precio_venta

            stock_inicial = int(self.stock_entry.get())
            stock_minimo = int(self.min_stock_entry.get() or 5)

            if precio_minimo > precio_base:
                messagebox.showwarning(
                    "Validación",
                    "El Precio Mínimo no puede ser mayor que el Precio Inicial."
                )
                return

            cursor.execute(
                """
                INSERT INTO productos
                    (nombre, descripcion, precio, precio_base, precio_minimo,
                     stock, categoria_id, stock_minimo, codigo_barras)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.name_entry.get(),
                    self.desc_entry.get(),
                    precio_venta,
                    precio_base,
                    precio_minimo,
                    stock_inicial,
                    categoria_id,
                    stock_minimo,
                    self.barcode_entry.get().strip() or None
                )
            )

            product_id = cursor.lastrowid

            image_path = self.image_selector.get_image_path()
            if image_path:
                final_image_path = self.image_manager.copy_image_to_app(
                    image_path, product_id
                )
                if final_image_path:
                    cursor.execute("PRAGMA table_info(productos)")
                    columns = [column[1] for column in cursor.fetchall()]
                    if "imagen_path" not in columns:
                        cursor.execute("ALTER TABLE productos ADD COLUMN imagen_path TEXT")

                    cursor.execute(
                        """
                        UPDATE productos SET imagen_path = ? WHERE id = ?
                        """,
                        (final_image_path, product_id)
                    )

            conn.commit()
            conn.close()

            messagebox.showinfo("Éxito", "Producto agregado correctamente")
            self.clear_form()
            self.load_products()

        except Exception as e:
            messagebox.showerror("Error", f"No se pudo agregar el producto: {str(e)}")

    def validate_fields(self):
        required_fields = [
            (self.name_entry, "Nombre"),
            (self.price_entry, "Precio venta"),
            (self.stock_entry, "Stock"),
        ]

        for field, name in required_fields:
            if not field.get().strip():
                messagebox.showwarning(
                    "Validación", f"El campo {name} es requerido"
                )
                field.focus()
                return False

        try:
            float(self.price_entry.get())
            if self.base_price_entry.get().strip():
                float(self.base_price_entry.get())
            if self.min_price_entry.get().strip():
                float(self.min_price_entry.get())
            int(self.stock_entry.get())
            if self.min_stock_entry.get():
                int(self.min_stock_entry.get())
        except ValueError:
            messagebox.showwarning(
                "Validación",
                "Precios deben ser números válidos y el Stock un número entero."
            )
            return False

        return True

    def clear_form(self):
        self.name_entry.delete(0, "end")
        self.desc_entry.delete(0, "end")
        self.price_entry.delete(0, "end")
        self.base_price_entry.delete(0, "end")
        self.min_price_entry.delete(0, "end")
        self.stock_entry.delete(0, "end")
        self.min_stock_entry.delete(0, "end")
        self.barcode_entry.delete(0, "end")

        self.category_combo.set("")
        self.image_selector.clear_image()

    def load_products(self, search_text=None, category_filter=None):
        for item in self.tree.get_children():
            self.tree.delete(item)

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(productos)")
        columns = [column[1] for column in cursor.fetchall()]
        has_img = "imagen_path" in columns

        if has_img:
            base_select = """
                SELECT p.id, p.nombre, p.descripcion, p.precio, p.stock,
                       c.nombre, p.stock_minimo, p.codigo_barras, p.imagen_path
                FROM productos p
                LEFT JOIN categorias c ON p.categoria_id = c.id
                WHERE p.activo = 1
            """
        else:
            base_select = """
                SELECT p.id, p.nombre, p.descripcion, p.precio, p.stock,
                       c.nombre, p.stock_minimo, p.codigo_barras
                FROM productos p
                LEFT JOIN categorias c ON p.categoria_id = c.id
                WHERE p.activo = 1
            """

        query = base_select
        params = []

        if search_text:
            query += """
                AND (
                    p.nombre LIKE ?
                    OR p.descripcion LIKE ?
                    OR p.codigo_barras LIKE ?
                )
            """
            like = f"%{search_text}%"
            params.extend([like, like, like])

        if category_filter and category_filter != "Todas":
            query += " AND c.nombre = ?"
            params.append(category_filter)

        # Calcular paginación basada en conteo
        total_items = self.db.get_total_inventory_count(search_text, category_filter)
        import math
        self.total_pages = math.ceil(total_items / self.limit_per_page)
        if self.total_pages < 1:
            self.total_pages = 1
            
        if self.current_page > self.total_pages:
            self.current_page = self.total_pages

        query += " ORDER BY p.nombre LIMIT ? OFFSET ?"
        offset = (self.current_page - 1) * self.limit_per_page
        params.extend([self.limit_per_page, offset])

        cursor.execute(query, params)

        # Actualizar UI de Paginación
        if hasattr(self, 'lbl_page'):
            self.lbl_page.configure(text=f"Página {self.current_page} de {self.total_pages}")
            
            # Deshabilitar botones en límites
            if self.current_page <= 1:
                self.btn_prev.configure(state="disabled")
            else:
                self.btn_prev.configure(state="normal")
                
            if self.current_page >= self.total_pages:
                self.btn_next.configure(state="disabled")
            else:
                self.btn_next.configure(state="normal")

        for row in cursor.fetchall():
            precio = row[3]
            if isinstance(precio, str) and "RD$" in str(precio):
                precio_formateado = precio
            else:
                precio_formateado = f"RD$ {float(precio):.2f}"

            row_formatted = list(row)
            row_formatted[3] = precio_formateado

            tags = ("low_stock",) if row[4] <= row[6] else ()
            self.tree.insert("", "end", values=row_formatted, tags=tags)

        self.tree.tag_configure("low_stock", background="#ff6b6b")

        conn.close()

    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            texto = self.search_entry.get().strip() if self.search_entry else ""
            categoria = self.category_filter.get() if self.category_filter else "Todas"
            self.load_products(search_text=texto, category_filter=categoria)

    def next_page(self):
        if self.current_page < self.total_pages:
            self.current_page += 1
            texto = self.search_entry.get().strip() if self.search_entry else ""
            categoria = self.category_filter.get() if self.category_filter else "Todas"
            self.load_products(search_text=texto, category_filter=categoria)

    # ---------- EDITAR / ELIMINAR / REPORTE ----------

    def edit_product(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(
                "Selección", "Por favor selecciona un producto para editar"
            )
            return

        product_data = self.tree.item(selected[0])["values"]
        product_id = product_data[0]

        self.create_edit_window(product_id, product_data)

    def create_edit_window(self, product_id, product_data):
        """
        Crear ventana para editar producto - incluye precios base / mínimo
        y campo de CÓDIGO DE BARRAS.
        """
        try:
            edit_window = tk.Toplevel(self.parent)
            edit_window.title(f"Editar Producto - ID: {product_id}")
            edit_window.geometry("700x680")
            edit_window.transient(self.parent)
            edit_window.grab_set()
            edit_window.configure(bg="#2b2b2b")
            edit_window.resizable(False, False)

            edit_window.update_idletasks()
            x = (edit_window.winfo_screenwidth() // 2) - (700 // 2)
            y = (edit_window.winfo_screenheight() // 2) - (680 // 2)
            edit_window.geometry(f"700x680+{x}+{y}")

            conn = self.db.get_connection()
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(productos)")
            cols = [c[1] for c in cursor.fetchall()]
            if "precio_base" not in cols:
                cursor.execute("ALTER TABLE productos ADD COLUMN precio_base REAL")
            if "precio_minimo" not in cols:
                cursor.execute("ALTER TABLE productos ADD COLUMN precio_minimo REAL")

            cursor.execute("PRAGMA table_info(productos)")
            cols = [c[1] for c in cursor.fetchall()]
            has_img = "imagen_path" in cols

            if has_img:
                cursor.execute(
                    """
                    SELECT p.id, p.nombre, p.descripcion,
                           p.precio, p.precio_base, p.precio_minimo,
                           p.stock, p.categoria_id, p.stock_minimo,
                           p.codigo_barras, p.imagen_path,
                           c.nombre as categoria_nombre
                    FROM productos p
                    LEFT JOIN categorias c ON p.categoria_id = c.id
                    WHERE p.id = ?
                    """,
                    (product_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT p.id, p.nombre, p.descripcion,
                           p.precio, p.precio_base, p.precio_minimo,
                           p.stock, p.categoria_id, p.stock_minimo,
                           p.codigo_barras, NULL as imagen_path,
                           c.nombre as categoria_nombre
                    FROM productos p
                    LEFT JOIN categorias c ON p.categoria_id = c.id
                    WHERE p.id = ?
                    """,
                    (product_id,),
                )

            product = cursor.fetchone()
            conn.close()

            (
                _pid,
                p_nombre,
                p_desc,
                p_precio,
                p_precio_base,
                p_precio_min,
                p_stock,
                p_cat_id,
                p_stock_min,
                p_codigo_barras,
                p_img_path,
                p_cat_name,
            ) = product

            if p_precio_base is None:
                p_precio_base = p_precio
            if p_precio_min is None:
                p_precio_min = p_precio

            self.edit_image_path = None

            title_label = tk.Label(
                edit_window,
                text=f"EDITAR PRODUCTO: {product_data[1]}",
                font=("Arial", 14, "bold"),
                fg="white",
                bg="#2b2b2b",
            )
            title_label.pack(pady=10)

            main_frame = tk.Frame(edit_window, bg="#2b2b2b")
            main_frame.pack(fill="both", expand=True, padx=20, pady=10)

            left_frame = tk.Frame(main_frame, bg="#2b2b2b")
            left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

            right_frame = tk.Frame(main_frame, bg="#2b2b2b", width=200)
            right_frame.pack(side="right", fill="y", padx=(10, 0))
            right_frame.pack_propagate(False)

            form_frame = tk.Frame(left_frame, bg="#2b2b2b")
            form_frame.pack(fill="both", expand=True)

            # Nombre
            tk.Label(
                form_frame,
                text="Nombre:",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(5, 3))
            name_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            name_entry.insert(0, p_nombre)
            name_entry.pack(fill="x", pady=(0, 8))

            # Descripción
            tk.Label(
                form_frame,
                text="Descripción:",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            desc_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            desc_entry.insert(0, p_desc or "")
            desc_entry.pack(fill="x", pady=(0, 8))

            # Código de barras
            tk.Label(
                form_frame,
                text="Código de Barras:",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            barcode_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            barcode_entry.insert(0, p_codigo_barras or "")
            barcode_entry.pack(fill="x", pady=(0, 8))

            # Precio inicial
            tk.Label(
                form_frame,
                text="Precio Inicial ($):",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            base_price_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            base_price_entry.insert(0, f"{float(p_precio_base):.2f}")
            base_price_entry.pack(fill="x", pady=(0, 8))

            # Precio venta
            tk.Label(
                form_frame,
                text="Precio Venta ($):",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            price_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            price_entry.insert(0, f"{float(p_precio):.2f}")
            price_entry.pack(fill="x", pady=(0, 8))

            # Precio mínimo
            tk.Label(
                form_frame,
                text="Precio Mínimo ($):",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            min_price_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            min_price_entry.insert(0, f"{float(p_precio_min):.2f}")
            min_price_entry.pack(fill="x", pady=(0, 8))

            # Stock
            tk.Label(
                form_frame,
                text="Stock:",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            stock_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            stock_entry.insert(0, str(p_stock))
            stock_entry.pack(fill="x", pady=(0, 8))

            # Categoría
            tk.Label(
                form_frame,
                text="Categoría:",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))

            category_frame = tk.Frame(form_frame, bg="#2b2b2b")
            category_frame.pack(fill="x", pady=(0, 8))

            categories = self.get_categories()
            category_var = tk.StringVar(edit_window)
            category_var.set(
                p_cat_name if p_cat_name else (categories[0] if categories else "")
            )

            category_dropdown = tk.OptionMenu(category_frame, category_var, *categories)
            category_dropdown.config(width=30, font=("Arial", 10))
            category_dropdown.pack(fill="x")

            # Stock mínimo
            tk.Label(
                form_frame,
                text="Stock Mínimo:",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 10, "bold"),
            ).pack(anchor="w", pady=(0, 3))
            min_stock_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            min_stock_entry.insert(0, str(p_stock_min or 0))
            min_stock_entry.pack(fill="x", pady=(0, 10))

            # Imagen
            tk.Label(
                right_frame,
                text="Imagen del Producto",
                fg="white",
                bg="#2b2b2b",
                font=("Arial", 12, "bold"),
            ).pack(pady=(8, 3))

            image_frame = tk.Frame(right_frame, bg="#3a3a3a", relief="sunken", bd=1)
            image_frame.pack(fill="both", expand=True, padx=5, pady=5)

            image_label = tk.Label(
                image_frame,
                text="Arrastra imagen aquí\no haz clic para seleccionar",
                bg="#3a3a3a",
                fg="white",
                font=("Arial", 10),
                wraplength=180,
                justify="center",
            )
            image_label.pack(expand=True, fill="both", padx=10, pady=10)

            def select_image():
                from tkinter import filedialog

                file_path = filedialog.askopenfilename(
                    title="Seleccionar imagen",
                    filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.gif *.bmp")],
                )
                if file_path:
                    self.edit_image_path = file_path
                    display_image(file_path)

            def display_image(image_path):
                try:
                    image = Image.open(image_path)
                    if image.width > 180 or image.height > 150:
                        image.thumbnail((180, 150), Image.Resampling.LANCZOS)

                    photo = ImageTk.PhotoImage(image)
                    image_label.configure(image=photo, text="")
                    image_label.image = photo
                except Exception as e:
                    messagebox.showerror(
                        "Error", f"No se pudo cargar la imagen: {str(e)}"
                    )

            current_image_path = None
            if p_img_path:
                current_image_path = p_img_path
                if os.path.exists(current_image_path):
                    display_image(current_image_path)
                    self.edit_image_path = current_image_path

            select_btn = tk.Button(
                right_frame,
                text="📁 Seleccionar",
                command=select_image,
                bg="#1f6aa5",
                fg="white",
                font=("Arial", 10, "bold"),
                width=15,
            )
            select_btn.pack(pady=(5, 3))

            def clear_image():
                self.edit_image_path = None
                image_label.configure(
                    image="", text="Arrastra imagen aquí\no haz clic para seleccionar"
                )

            clear_btn = tk.Button(
                right_frame,
                text="🗑️ Quitar imagen",
                command=clear_image,
                bg="#d9534f",
                fg="white",
                font=("Arial", 10, "bold"),
                width=15,
            )
            clear_btn.pack(pady=(0, 5))

            button_frame = tk.Frame(main_frame, bg="#2b2b2b")
            button_frame.pack(fill="x", pady=10)

            def guardar_cambios():
                try:
                    if not name_entry.get().strip():
                        messagebox.showerror("Error", "El campo Nombre es requerido")
                        name_entry.focus()
                        return

                    if not price_entry.get().strip():
                        messagebox.showerror("Error", "El campo Precio Venta es requerido")
                        price_entry.focus()
                        return

                    if not stock_entry.get().strip():
                        messagebox.showerror("Error", "El campo Stock es requerido")
                        stock_entry.focus()
                        return

                    try:
                        precio_base_val = float(base_price_entry.get() or price_entry.get())
                        precio_venta_val = float(price_entry.get())
                        precio_min_val = float(min_price_entry.get() or price_entry.get())
                        stock_valor = int(stock_entry.get())
                    except ValueError:
                        messagebox.showerror(
                            "Error",
                            "Precios deben ser números y Stock un número entero"
                        )
                        return

                    if precio_min_val > precio_base_val:
                        messagebox.showwarning(
                            "Validación",
                            "El Precio Mínimo no puede ser mayor que el Precio Inicial."
                        )
                        return

                    conn = self.db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT id FROM categorias WHERE nombre = ?",
                        (category_var.get(),),
                    )
                    result = cursor.fetchone()

                    if not result:
                        messagebox.showerror("Error", "Categoría no válida")
                        return

                    categoria_id = result[0]

                    cursor.execute(
                        """
                        UPDATE productos
                        SET nombre = ?, descripcion = ?, precio = ?,
                            precio_base = ?, precio_minimo = ?,
                            stock = ?, categoria_id = ?, stock_minimo = ?,
                            codigo_barras = ?
                        WHERE id = ?
                        """,
                        (
                            name_entry.get().strip(),
                            desc_entry.get().strip(),
                            precio_venta_val,
                            precio_base_val,
                            precio_min_val,
                            stock_valor,
                            categoria_id,
                            int(min_stock_entry.get() or 5),
                            barcode_entry.get().strip() or None,
                            product_id,
                        ),
                    )

                    if self.edit_image_path and self.edit_image_path != current_image_path:
                        final_image_path = self.image_manager.copy_image_to_app(
                            self.edit_image_path, product_id
                        )
                        if final_image_path:
                            cursor.execute("PRAGMA table_info(productos)")
                            cols = [c[1] for c in cursor.fetchall()]
                            if "imagen_path" not in cols:
                                cursor.execute(
                                    "ALTER TABLE productos ADD COLUMN imagen_path TEXT"
                                )
                            cursor.execute(
                                """
                                UPDATE productos
                                SET imagen_path = ? WHERE id = ?
                                """,
                                (final_image_path, product_id),
                            )

                    conn.commit()
                    conn.close()

                    messagebox.showinfo("Éxito", "Producto actualizado correctamente")
                    edit_window.destroy()
                    self.load_products()

                except Exception as e:
                    messagebox.showerror(
                        "Error", f"No se pudo actualizar el producto: {str(e)}"
                    )

            save_btn = tk.Button(
                button_frame,
                text="💾 GUARDAR",
                command=guardar_cambios,
                bg="#2fa572",
                fg="white",
                font=("Arial", 11, "bold"),
                width=18,
                height=2,
            )
            save_btn.pack(side="left", padx=8)

            cancel_btn = tk.Button(
                button_frame,
                text="❌ CANCELAR",
                command=edit_window.destroy,
                bg="#d9534f",
                fg="white",
                font=("Arial", 11, "bold"),
                width=14,
                height=2,
            )
            cancel_btn.pack(side="right", padx=8)

            edit_window.focus_set()
            edit_window.wait_window()

        except Exception as e:
            messagebox.showerror(
                "Error", f"No se pudo abrir la ventana de edición: {str(e)}"
            )

    def delete_product(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(
                "Selección", "Por favor selecciona un producto para eliminar"
            )
            return

        product_id = self.tree.item(selected[0])["values"][0]
        product_name = self.tree.item(selected[0])["values"][1]

        confirm = messagebox.askyesno(
            "Confirmar Eliminación",
            f"¿Estás seguro de eliminar el producto?\n\n"
            f"Producto: {product_name}\n"
            f"ID: {product_id}\n\n"
            f"Esta acción no se puede deshacer.",
        )

        if confirm:
            try:
                conn = self.db.get_connection()
                cursor = conn.cursor()

                self.image_manager.delete_product_image(product_id)

                cursor.execute("UPDATE productos SET activo = 0 WHERE id = ?", (product_id,))
                conn.commit()
                conn.close()

                messagebox.showinfo(
                    "Éxito", f"Producto '{product_name}' eliminado correctamente"
                )
                self.load_products()

            except Exception as e:
                messagebox.showerror(
                    "Error", f"No se pudo eliminar el producto: {str(e)}"
                )

    def low_stock_report(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT p.nombre, p.stock, p.stock_minimo, c.nombre
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE p.stock <= p.stock_minimo AND p.activo = 1
            ORDER BY p.stock ASC
            """
        )

        low_stock_products = cursor.fetchall()
        conn.close()

        if not low_stock_products:
            messagebox.showinfo("Stock", "✅ No hay productos con stock bajo")
            return

        report = "📊 PRODUCTOS CON STOCK BAJO:\n\n"
        for product in low_stock_products:
            report += f"• {product[0]} ({product[3]})\n"
            report += f"  Stock actual: {product[1]} | Mínimo: {product[2]}\n\n"

        messagebox.showwarning("Stock Bajo", report)
