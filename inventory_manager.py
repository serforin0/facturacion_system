import csv
import math
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

from database import Database
from image_manager import ImageManager
from kardex_manager import KardexPanel
from producto_dialog import ProductoDialog
from styles import Styles


class InventoryManager:
    """Inventario con lista tipo ERP, pestañas superiores y barra de acciones inferior."""

    def __init__(self, parent_frame, app=None):
        self.db = Database()
        self.image_manager = ImageManager()
        self.parent = parent_frame
        self.app = app

        self.search_entry = None
        self.search_mode_combo = None
        self.category_filter = None
        self.current_page = 1
        self.limit_per_page = 50
        self.total_pages = 1
        self.tree = None
        self.lbl_count = None
        self.btn_prev = None
        self.btn_next = None
        self.lbl_page = None
        self.var_incluir_inactivos = ctk.BooleanVar(value=False)
        self.mode_container = None
        self.inventory_outer = None
        self.kardex_outer = None
        self.kardex_panel = None

        self.setup_ui()
        self.load_products()

    def _toplevel_for_dialogs(self):
        w = self.parent
        while w is not None and getattr(w, "master", None) is not None:
            w = w.master
        return w if w is not None else self.parent

    def _search_mode_key(self) -> str:
        if not self.search_mode_combo:
            return "todos"
        m = self.search_mode_combo.get()
        return {
            "Código": "codigo",
            "Nombre": "nombre",
            "Descripción": "descripcion",
            "Todo": "todos",
        }.get(m, "todos")

    def _search_clause(self, search_text: str):
        if not (search_text or "").strip():
            return "", []
        like = f"%{search_text.strip()}%"
        sm = self._search_mode_key().lower()
        if sm == "codigo":
            return (
                " AND (IFNULL(p.codigo_producto,'') LIKE ? OR IFNULL(p.codigo_barras,'') LIKE ?)",
                [like, like],
            )
        if sm == "nombre":
            return " AND p.nombre LIKE ?", [like]
        if sm == "descripcion":
            return " AND IFNULL(p.descripcion,'') LIKE ?", [like]
        return (
            " AND (p.nombre LIKE ? OR IFNULL(p.descripcion,'') LIKE ? "
            "OR IFNULL(p.codigo_barras,'') LIKE ? OR IFNULL(p.codigo_producto,'') LIKE ?)",
            [like, like, like, like],
        )

    def setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, **Styles.get_frame_style())
        self.main_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self._setup_top_tabs()
        self.mode_container = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.mode_container.pack(fill="both", expand=True, padx=0, pady=0)
        self._setup_filters_and_table()
        self._setup_bottom_toolbar()

    def _setup_top_tabs(self):
        bar = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        bar.pack(fill="x", padx=6, pady=(4, 2))

        ctk.CTkLabel(
            bar, text="Inventario", font=("Arial", 14, "bold")
        ).pack(side="left", padx=(0, 14))

        seg = ctk.CTkFrame(bar, fg_color="transparent")
        seg.pack(side="left")
        ctk.CTkButton(
            seg,
            text="Lista de productos",
            width=140,
            height=30,
            font=("Arial", 11, "bold"),
            fg_color="#475569",
            hover_color="#334155",
            command=self._show_inventory_mode,
        ).pack(side="left", padx=3, pady=2)
        ctk.CTkButton(
            seg,
            text="Kardex (aquí)",
            width=120,
            height=30,
            font=("Arial", 11, "bold"),
            fg_color="#64748B",
            hover_color="#334155",
            command=self._show_kardex_mode,
        ).pack(side="left", padx=3, pady=2)

        ctk.CTkLabel(
            bar,
            text="Otros módulos: menú superior de la aplicación",
            font=("Arial", 10),
            text_color="gray",
        ).pack(side="right", padx=8)

    def _show_inventory_mode(self):
        if self.kardex_outer is not None:
            self.kardex_outer.pack_forget()
        if self.inventory_outer is not None:
            self.inventory_outer.pack(fill="both", expand=True)

    def _show_kardex_mode(self):
        if self.inventory_outer is not None:
            self.inventory_outer.pack_forget()
        if self.kardex_outer is None:
            self.kardex_outer = ctk.CTkFrame(self.mode_container, fg_color="transparent")
            cu = getattr(self.app, "current_user", None) if self.app else None
            self.kardex_panel = KardexPanel(
                self.kardex_outer,
                self.db,
                self.image_manager,
                current_user=cu,
                on_refresh_products=self.load_products,
            )
            self.kardex_panel.pack(fill="both", expand=True)
        self.kardex_outer.pack(fill="both", expand=True)
        sel = self.tree.selection() if self.tree else ()
        if sel and self.kardex_panel:
            try:
                self.kardex_panel.set_product(int(sel[0]))
            except (TypeError, ValueError):
                pass

    def _setup_filters_and_table(self):
        box = ctk.CTkFrame(self.mode_container, **Styles.get_frame_style())
        box.pack(fill="both", expand=True)
        self.inventory_outer = box

        filt = ctk.CTkFrame(box, fg_color="transparent")
        filt.pack(fill="x", padx=8, pady=6)

        ctk.CTkLabel(filt, text="Productos por:", font=("Arial", 11)).pack(
            side="left", padx=(0, 6)
        )
        self.search_mode_combo = ctk.CTkComboBox(
            filt,
            values=["Código", "Nombre", "Descripción", "Todo"],
            width=130,
            height=28,
        )
        self.search_mode_combo.set("Código")
        self.search_mode_combo.pack(side="left", padx=(0, 10))

        self.search_entry = ctk.CTkEntry(
            filt, placeholder_text="Buscar…", width=280, height=28
        )
        self.search_entry.pack(side="left", padx=(0, 8))
        self.search_entry.bind("<Return>", lambda e: self.search_products())

        ctk.CTkLabel(filt, text="Categoría:", font=("Arial", 11)).pack(
            side="left", padx=(8, 6)
        )
        self.category_filter = ctk.CTkComboBox(
            filt,
            values=["Todas"] + self.get_categories(),
            width=150,
            height=28,
        )
        self.category_filter.set("Todas")
        self.category_filter.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            filt, text="Buscar", width=80, height=28, command=self.search_products
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            filt,
            text="Limpiar",
            width=70,
            height=28,
            fg_color="#6B7280",
            command=self.clear_search,
        ).pack(side="left", padx=4)

        ctk.CTkCheckBox(
            filt,
            text="Incluir inactivos",
            variable=self.var_incluir_inactivos,
            width=120,
            command=self._on_filtro_inactivos,
        ).pack(side="left", padx=(12, 4))

        ctk.CTkButton(
            filt,
            text="Exportar CSV",
            width=100,
            height=28,
            fg_color="#059669",
            hover_color="#047857",
            command=self.exportar_csv_vista,
        ).pack(side="left", padx=4)

        self.lbl_count = ctk.CTkLabel(
            filt, text="", font=("Arial", 11), text_color="gray"
        )
        self.lbl_count.pack(side="right", padx=8)

        ctk.CTkButton(
            filt,
            text="Stock bajo",
            width=100,
            height=28,
            fg_color=Styles.WARNING,
            command=self.low_stock_report,
        ).pack(side="right", padx=4)

        ctk.CTkLabel(
            filt,
            text="F2 editar · F3 buscar · F5 actualizar",
            font=("Arial", 10),
            text_color="gray60",
        ).pack(side="right", padx=(4, 12))

        tree_frame = ctk.CTkFrame(box)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=26,
            fieldbackground="#2a2d2e",
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#22559b")])

        cols = (
            "Código",
            "Descripción",
            "P. venta",
            "Tipo",
            "Stock",
            "Medida",
            "Bodega",
            "Categoría",
            "Estado",
            "Ult. venta",
        )
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings", height=18
        )
        widths = (96, 220, 82, 72, 56, 72, 52, 100, 68, 92)
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="center")
        self.tree.column("Descripción", anchor="w")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda e: self.edit_product())
        self.tree.bind("<F2>", lambda e: self.edit_product())
        self.tree.bind("<F3>", lambda e: self._focus_search())
        self.tree.bind("<F5>", lambda e: self.load_products())
        self.search_entry.bind("<F5>", lambda e: self.load_products())
        self.search_entry.bind("<F3>", lambda e: self._focus_search())
        self.main_frame.bind("<F3>", lambda e: self._focus_search())

        pag = ctk.CTkFrame(box, fg_color="transparent")
        pag.pack(fill="x", padx=8, pady=(0, 6))

        self.btn_prev = ctk.CTkButton(
            pag, text="< Anterior", width=90, height=26, command=self.prev_page
        )
        self.btn_prev.pack(side="left", padx=2)
        self.lbl_page = ctk.CTkLabel(pag, text="Página 1 de 1", font=("Arial", 11, "bold"))
        self.lbl_page.pack(side="left", padx=12)
        self.btn_next = ctk.CTkButton(
            pag, text="Siguiente >", width=90, height=26, command=self.next_page
        )
        self.btn_next.pack(side="left", padx=2)

    def _setup_bottom_toolbar(self):
        bar = ctk.CTkFrame(self.main_frame, fg_color=("#E8EDF3", "#252525"))
        bar.pack(fill="x", padx=6, pady=(2, 6))
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=10)

        actions = [
            ("Crear\nproducto", self._crear_producto, "#0D9488"),
            ("Duplicar", self._duplicar_producto, "#0F766E"),
            ("Modif.\nprod.", self.edit_product, "#2563EB"),
            ("Modif.\nprecios", self._modif_precios, "#CA8A04"),
            ("Eliminar\nprod.", self.delete_product, "#B91C1C"),
            ("Transferir", self._transferir, "#4F46E5"),
            ("Ver\nproducto", self._ver_producto, "#475569"),
            ("Etiquetas", self._etiquetas, "#64748B"),
            ("Notas", self._notas_producto, "#64748B"),
            ("Salir", self._salir_inventario, "#334155"),
        ]
        for txt, cmd, col in actions:
            wbtn = 92 if "\n" in txt else 88
            ctk.CTkButton(
                inner,
                text=txt,
                width=wbtn,
                height=76,
                font=("Arial", 11, "bold"),
                fg_color=col,
                hover_color="#1e293b",
                command=cmd,
            ).pack(side="left", padx=5, pady=4)

    def get_categories(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT nombre FROM categorias ORDER BY nombre")
        categories = [row[0] for row in cursor.fetchall()]
        conn.close()
        return categories

    def _focus_search(self, event=None):
        if self.search_entry:
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")
        return "break"

    def clear_search(self):
        if self.search_entry:
            self.search_entry.delete(0, "end")
        if self.category_filter:
            self.category_filter.set("Todas")
        self.var_incluir_inactivos.set(False)
        self.current_page = 1
        self.load_products()

    def _on_filtro_inactivos(self):
        self.current_page = 1
        self.load_products()

    def search_products(self):
        self.current_page = 1
        texto = self.search_entry.get().strip() if self.search_entry else ""
        categoria = self.category_filter.get() if self.category_filter else "Todas"
        self.load_products(search_text=texto, category_filter=categoria)

    def _crear_producto(self):
        ProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.image_manager,
            product_id=None,
            read_only=False,
            on_saved=lambda: self.load_products(),
        )

    def _duplicar_producto(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(
                "Duplicar", "Seleccione un producto en la lista para duplicarlo."
            )
            return
        pid = int(selected[0])
        ProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.image_manager,
            product_id=None,
            duplicate_from_id=pid,
            read_only=False,
            on_saved=lambda: self.load_products(),
        )

    def _modif_precios(self):
        """Abre el mismo formulario; el usuario ajusta la sección de precios."""
        self.edit_product()

    def _transferir(self):
        messagebox.showinfo(
            "Transferir",
            "Transferencias entre bodegas no están implementadas aún.",
        )

    def _ver_producto(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Selección", "Seleccione un producto en la lista.")
            return
        pid = int(sel[0])
        ProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.image_manager,
            product_id=pid,
            read_only=True,
            on_saved=None,
        )

    def _etiquetas(self):
        messagebox.showinfo(
            "Etiquetas",
            "Impresión de etiquetas: pendiente de integrar.",
        )

    def _notas_producto(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Selección", "Seleccione un producto.")
            return
        pid = int(sel[0])
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT IFNULL(notas_internas,''), IFNULL(descripcion,'') FROM productos WHERE id=?",
            (pid,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return
        n, d = row
        msg = ""
        if d:
            msg += f"Descripción:\n{d}\n\n"
        if n:
            msg += f"Notas internas:\n{n}"
        if not msg.strip():
            msg = "Sin notas ni descripción larga."
        messagebox.showinfo("Notas del producto", msg[:1800])

    def _salir_inventario(self):
        if self.app is not None:
            self.app.show_dashboard()
        else:
            messagebox.showinfo("Salir", "Vuelva al inicio desde la barra superior.")

    def load_products(self, search_text=None, category_filter=None):
        for item in self.tree.get_children():
            self.tree.delete(item)

        texto = (
            search_text
            if search_text is not None
            else (self.search_entry.get().strip() if self.search_entry else "")
        )
        categoria = (
            category_filter
            if category_filter is not None
            else (self.category_filter.get() if self.category_filter else "Todas")
        )

        sm = self._search_mode_key()
        solo_activos = not self.var_incluir_inactivos.get()
        total_items = self.db.get_total_inventory_count(
            texto, categoria, sm, solo_activos=solo_activos
        )
        self.total_pages = max(1, math.ceil(total_items / self.limit_per_page))
        if self.current_page > self.total_pages:
            self.current_page = self.total_pages

        offset = (self.current_page - 1) * self.limit_per_page

        conn = self.db.get_connection()
        cursor = conn.cursor()

        extra, params = self._search_clause(texto)

        query = """
            SELECT p.id,
                   COALESCE(NULLIF(TRIM(p.codigo_producto), ''),
                            NULLIF(TRIM(p.codigo_barras), ''),
                            printf('P-%05d', p.id)),
                   p.nombre,
                   p.precio,
                   IFNULL(p.tipo_producto, 'Físico'),
                   p.stock,
                   IFNULL(p.unidad_medida, 'Unidad'),
                   IFNULL(NULLIF(TRIM(p.bodega_codigo), ''), '—'),
                   IFNULL(c.nombre, '—'),
                   CASE WHEN p.activo THEN 'Activo' ELSE 'Inactivo' END,
                   IFNULL(p.stock_minimo, 0),
                   (SELECT strftime('%d/%m/%Y', MAX(f.fecha), 'localtime')
                    FROM factura_detalle fd
                    JOIN facturas f ON f.id = fd.factura_id
                    WHERE fd.producto_id = p.id
                      AND IFNULL(f.estado, '') != 'anulada')
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE 1=1
        """
        query += extra
        qparams = list(params)
        if solo_activos:
            query += " AND p.activo = 1"

        if categoria and categoria != "Todas":
            query += " AND c.nombre = ?"
            qparams.append(categoria)

        query += " ORDER BY p.nombre LIMIT ? OFFSET ?"
        qparams.extend([self.limit_per_page, offset])

        cursor.execute(query, qparams)
        rows_f = cursor.fetchall()

        n_show = len(rows_f)
        if self.lbl_count:
            if total_items == 0:
                self.lbl_count.configure(text="0 productos")
            else:
                start_i = offset + 1
                end_i = offset + n_show
                self.lbl_count.configure(
                    text=f"Filas {start_i}–{end_i} de {total_items} producto(s)"
                )

        for row in rows_f:
            (
                pid,
                codigo,
                nombre,
                precio,
                tipo,
                stock,
                medida,
                bodega,
                categ,
                estado,
                stock_min,
                ult_v,
            ) = row
            precio_s = f"{float(precio or 0):,.2f}" if precio is not None else "0.00"
            ult = ult_v if ult_v else "—"
            smin = float(stock_min or 0)
            st = float(stock or 0)
            if st <= 0:
                tags = ("agotado",)
            elif smin > 0 and st <= smin:
                tags = ("low_stock",)
            else:
                tags = ()

            stock_show = int(st) if st.is_integer() else round(st, 4)

            self.tree.insert(
                "",
                "end",
                iid=str(pid),
                values=(
                    codigo,
                    nombre,
                    precio_s,
                    tipo,
                    stock_show,
                    medida,
                    bodega or "—",
                    categ,
                    estado,
                    ult,
                ),
                tags=tags,
            )

        self.tree.tag_configure("low_stock", background="#7f1d1d")
        self.tree.tag_configure("agotado", background="#78350f")

        conn.close()

        if self.lbl_page:
            self.lbl_page.configure(
                text=f"Página {self.current_page} de {self.total_pages}"
            )
        if self.btn_prev:
            self.btn_prev.configure(
                state="disabled" if self.current_page <= 1 else "normal"
            )
        if self.btn_next:
            self.btn_next.configure(
                state="disabled"
                if self.current_page >= self.total_pages
                else "normal"
            )

    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.load_products()

    def next_page(self):
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.load_products()

    def edit_product(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(
                "Selección", "Seleccione un producto para modificar."
            )
            return
        product_id = int(selected[0])
        ProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.image_manager,
            product_id=product_id,
            read_only=False,
            on_saved=lambda: self.load_products(),
        )

    def delete_product(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning(
                "Selección", "Seleccione un producto para eliminar."
            )
            return

        product_id = int(selected[0])
        vals = self.tree.item(selected[0])["values"]
        product_name = vals[1] if vals else str(product_id)

        confirm = messagebox.askyesno(
            "Confirmar",
            f"¿Eliminar el producto «{product_name}»?\n"
            f"Se desactivará en el sistema (no se borra el historial).",
        )

        if confirm:
            try:
                conn = self.db.get_connection()
                cursor = conn.cursor()
                self.image_manager.delete_product_image(product_id)
                cursor.execute(
                    "UPDATE productos SET activo = 0 WHERE id = ?", (product_id,)
                )
                conn.commit()
                conn.close()
                messagebox.showinfo("Listo", "Producto desactivado.")
                self.load_products()
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def exportar_csv_vista(self):
        """Exporta a CSV los productos que coinciden con filtros actuales (sin paginar)."""
        texto = self.search_entry.get().strip() if self.search_entry else ""
        categoria = self.category_filter.get() if self.category_filter else "Todas"
        solo_activos = not self.var_incluir_inactivos.get()
        sm = self._search_mode_key()
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")],
            initialfile=f"inventario_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        )
        if not path:
            return

        extra, params = self._search_clause(texto)
        query = """
            SELECT p.id,
                   COALESCE(NULLIF(TRIM(p.codigo_producto), ''),
                            NULLIF(TRIM(p.codigo_barras), ''),
                            printf('P-%05d', p.id)),
                   p.nombre,
                   p.precio,
                   p.precio_base,
                   IFNULL(p.tipo_producto, 'Físico'),
                   p.stock,
                   IFNULL(p.stock_minimo, 0),
                   IFNULL(p.unidad_medida, 'Unidad'),
                   IFNULL(NULLIF(TRIM(p.bodega_codigo), ''), ''),
                   IFNULL(c.nombre, ''),
                   CASE WHEN p.activo THEN 'Activo' ELSE 'Inactivo' END,
                   IFNULL(p.ubicacion, ''),
                   IFNULL(p.codigo_barras, '')
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE 1=1
        """
        query += extra
        qparams = list(params)
        if solo_activos:
            query += " AND p.activo = 1"
        if categoria and categoria != "Todas":
            query += " AND c.nombre = ?"
            qparams.append(categoria)
        query += " ORDER BY p.nombre"

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(query, qparams)
        rows = cursor.fetchall()
        conn.close()

        headers = (
            "id",
            "codigo",
            "nombre",
            "precio_venta",
            "precio_costo_base",
            "tipo",
            "stock",
            "stock_minimo",
            "unidad",
            "bodega_codigo",
            "categoria",
            "estado",
            "ubicacion",
            "codigo_barras",
        )
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(headers)
                for r in rows:
                    w.writerow(list(r))
            messagebox.showinfo(
                "Exportar CSV",
                f"Se exportaron {len(rows)} producto(s).\n\n{path}",
            )
        except OSError as e:
            messagebox.showerror("Exportar", f"No se pudo guardar el archivo:\n{e}")

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
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            messagebox.showinfo("Stock", "No hay productos con stock bajo.")
            return

        report = "Productos con stock bajo:\n\n"
        for product in rows:
            report += f"• {product[0]} ({product[3]})\n"
            report += f"  Stock: {product[1]} | Mínimo: {product[2]}\n\n"
        messagebox.showwarning("Stock bajo", report[:2000])
