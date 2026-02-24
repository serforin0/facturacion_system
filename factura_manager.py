import customtkinter as ctk
from tkinter import messagebox, ttk
from PIL import Image
import os
import sys
import tempfile
import subprocess
from datetime import datetime

from database import Database


class FacturaManager:
    def __init__(self, parent, current_user=None):
        self.parent = parent
        self.db = Database()
        self.current_user = current_user  # usuario logueado

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
        self.impuestos_total = 0.0         # sin ITBIS
        self.total_factura = 0.0           # ahora es igual al subtotal_total

        # Producto actual seleccionado después de una búsqueda
        # Tuple: (id, nombre, precio, precio_base, precio_minimo, stock, codigo_barras, imagen_path)
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

        self._setup_ui()

    # ==========================================
    #               UI
    # ==========================================

    def _setup_ui(self):
        # Frame principal del módulo
        main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Frame central con dos columnas: izquierda (búsqueda) y derecha (detalle)
        center_frame = ctk.CTkFrame(main_frame, fg_color="#2B2B2B")
        center_frame.pack(fill="both", expand=True)

        # ---------------------------------
        #   COLUMNA IZQUIERDA
        # ---------------------------------
        left_frame = ctk.CTkFrame(center_frame, fg_color="#2B2B2B")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 5), pady=0)

        # Bloque búsqueda
        search_frame = ctk.CTkFrame(left_frame, fg_color="#1F1F1F")
        search_frame.pack(fill="x", padx=5, pady=(0, 5))

        lbl_buscar = ctk.CTkLabel(
            search_frame,
            text="Buscar producto (CB / ID / Nombre):",
            font=("Arial", 12)
        )
        lbl_buscar.grid(row=0, column=0, padx=6, pady=6, sticky="w")

        self.entry_buscar = ctk.CTkEntry(search_frame, width=200)
        self.entry_buscar.grid(row=0, column=1, padx=5, pady=6, sticky="w")

        btn_buscar = ctk.CTkButton(
            search_frame,
            text="Buscar",
            width=70,
            command=self.buscar_producto
        )
        btn_buscar.grid(row=0, column=2, padx=6, pady=6)

        # Enter para buscar
        self.entry_buscar.bind("<Return>", lambda event: self.buscar_producto())

        # Resultados
        resultados_frame = ctk.CTkFrame(left_frame, fg_color="#1F1F1F")
        resultados_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.lista_resultados = ctk.CTkTextbox(
            resultados_frame,
            height=6,
            font=("Arial", 10),
        )
        self.lista_resultados.pack(fill="both", expand=True, padx=4, pady=4)

        self.lista_resultados.configure(cursor="hand2")
        self.lista_resultados.bind("<Button-1>", self._on_result_click)

        # Cantidad + descuentos + agregar
        cantidad_frame = ctk.CTkFrame(left_frame, fg_color="#1F1F1F")
        cantidad_frame.pack(fill="x", padx=5, pady=(5, 0))

        cantidad_frame.grid_columnconfigure(0, weight=0)
        cantidad_frame.grid_columnconfigure(1, weight=0)
        cantidad_frame.grid_columnconfigure(2, weight=1)
        cantidad_frame.grid_columnconfigure(3, weight=1)

        # Fila 0: cantidad + botón agregar
        lbl_cant = ctk.CTkLabel(
            cantidad_frame,
            text="Cantidad:",
            font=("Arial", 12)
        )
        lbl_cant.grid(row=0, column=0, padx=6, pady=4, sticky="w")

        self.entry_cantidad = ctk.CTkEntry(cantidad_frame, width=70)
        self.entry_cantidad.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        btn_agregar = ctk.CTkButton(
            cantidad_frame,
            text="Agregar",
            width=120,
            command=self.agregar_a_factura
        )
        btn_agregar.grid(row=0, column=2, columnspan=2, padx=6, pady=4, sticky="w")

        # Fila 1: descuento por ítem
        lbl_desc_pct = ctk.CTkLabel(
            cantidad_frame,
            text="Desc %:",
            font=("Arial", 11)
        )
        lbl_desc_pct.grid(row=1, column=0, padx=6, pady=4, sticky="w")

        self.entry_desc_pct = ctk.CTkEntry(cantidad_frame, width=60)
        self.entry_desc_pct.grid(row=1, column=1, padx=4, pady=4, sticky="w")

        lbl_desc_monto = ctk.CTkLabel(
            cantidad_frame,
            text="Desc $:",
            font=("Arial", 11)
        )
        lbl_desc_monto.grid(row=1, column=2, padx=6, pady=4, sticky="w")

        self.entry_desc_monto = ctk.CTkEntry(cantidad_frame, width=70)
        self.entry_desc_monto.grid(row=1, column=3, padx=4, pady=4, sticky="w")

        # Atajos de teclado
        try:
            self.parent.bind("<F2>", lambda e: self.finalizar_factura())
            self.parent.bind("<F3>", lambda e: self.entry_buscar.focus_set())
            self.parent.bind("<Delete>", lambda e: self.eliminar_item_factura())
        except Exception:
            pass

        # ---------------------------------
        #   COLUMNA DERECHA
        # ---------------------------------
        right_frame = ctk.CTkFrame(center_frame, fg_color="#2B2B2B")
        right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0), pady=0)

        # Zona superior: info producto + imagen
        top_frame = ctk.CTkFrame(right_frame, fg_color="#1F1F1F")
        top_frame.pack(fill="x", padx=5, pady=(0, 4))

        self.product_info_label = ctk.CTkLabel(
            top_frame,
            text="Producto seleccionado: ninguno",
            font=("Arial", 12),
            text_color="white",
            anchor="w"
        )
        self.product_info_label.pack(side="left", padx=6, pady=6, fill="x", expand=True)

        self.product_image_label = ctk.CTkLabel(
            top_frame,
            text="Sin imagen",
            width=110,
            height=80
        )
        self.product_image_label.pack(side="right", padx=6, pady=6)

        # Detalle de la factura (Treeview)
        detalle_frame = ctk.CTkFrame(right_frame, fg_color="#1F1F1F")
        detalle_frame.pack(fill="both", expand=True, padx=5, pady=4)

        lbl_detalle = ctk.CTkLabel(
            detalle_frame,
            text="Detalle de la Factura",
            font=("Arial", 13, "bold"),
            text_color="white"
        )
        lbl_detalle.pack(pady=(4, 2))

        tree_container = ctk.CTkFrame(detalle_frame, fg_color="#1F1F1F")
        tree_container.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "FacturaTreeview.Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=22,
            fieldbackground="#2a2d2e",
            borderwidth=0
        )
        style.configure(
            "FacturaTreeview.Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 10, "bold")
        )
        style.map(
            "FacturaTreeview.Treeview",
            background=[("selected", "#22559b")]
        )

        # SIN columna ITBIS
        self.tree_factura = ttk.Treeview(
            tree_container,
            columns=("Nombre", "Cant", "P.Unit", "Desc", "Subt", "Total"),
            show="headings",
            style="FacturaTreeview.Treeview"
        )

        cols = [
            ("Nombre", 150),
            ("Cant", 60),
            ("P.Unit", 75),
            ("Desc", 75),
            ("Subt", 85),
            ("Total", 85),
        ]
        for col, width in cols:
            self.tree_factura.heading(col, text=col)
            self.tree_factura.column(col, width=width, anchor="center")

        scrollbar_detalle = ttk.Scrollbar(
            tree_container,
            orient="vertical",
            command=self.tree_factura.yview
        )
        self.tree_factura.configure(yscrollcommand=scrollbar_detalle.set)
        scrollbar_detalle.pack(side="right", fill="y")

        self.tree_factura.pack(side="left", fill="both", expand=True)
        self.tree_factura.bind("<Double-1>", lambda e: self.editar_item_factura())

        # Botones de edición
        item_btn_frame = ctk.CTkFrame(detalle_frame, fg_color="#1F1F1F")
        item_btn_frame.pack(fill="x", padx=4, pady=(0, 4))

        ctk.CTkButton(
            item_btn_frame,
            text="✏️ Editar",
            width=100,
            command=self.editar_item_factura
        ).pack(side="left", padx=4, pady=4)

        ctk.CTkButton(
            item_btn_frame,
            text="🗑️ Eliminar",
            width=100,
            fg_color="#c0392b",
            command=self.eliminar_item_factura
        ).pack(side="left", padx=4, pady=4)

        ctk.CTkButton(
            item_btn_frame,
            text="🧹 Vaciar",
            width=100,
            fg_color="#7f8c8d",
            command=self.vaciar_factura
        ).pack(side="left", padx=4, pady=4)

        # Totales + botón finalizar
        totales_frame = ctk.CTkFrame(right_frame, fg_color="#1F1F1F")
        totales_frame.pack(fill="x", padx=5, pady=(4, 0))

        self.lbl_subtotal = ctk.CTkLabel(
            totales_frame,
            text="Subtotal: RD$ 0.00",
            font=("Arial", 12),
            text_color="white"
        )
        self.lbl_subtotal.grid(row=0, column=0, padx=6, pady=2, sticky="w")

        self.lbl_descuentos = ctk.CTkLabel(
            totales_frame,
            text="Descuentos: RD$ 0.00",
            font=("Arial", 12),
            text_color="white"
        )
        self.lbl_descuentos.grid(row=1, column=0, padx=6, pady=2, sticky="w")

        self.lbl_total = ctk.CTkLabel(
            totales_frame,
            text="Total: RD$ 0.00",
            font=("Arial", 14, "bold"),
            text_color="lightgreen"
        )
        self.lbl_total.grid(row=0, column=1, rowspan=2, padx=6, pady=2, sticky="e")

        btn_finalizar = ctk.CTkButton(
            totales_frame,
            text="Finalizar",
            fg_color="#2B5F87",
            font=("Arial", 12, "bold"),
            width=140,
            height=28,
            command=self.finalizar_factura
        )
        btn_finalizar.grid(row=0, column=2, rowspan=2, padx=6, pady=4, sticky="e")

        btn_desc_global = ctk.CTkButton(
            totales_frame,
            text="🛈 Desc. global",
            fg_color="#8e44ad",
            width=140,
            command=self._abrir_descuento_global_dialog
        )
        btn_desc_global.grid(row=2, column=0, padx=6, pady=4, sticky="w")

        totales_frame.grid_columnconfigure(0, weight=1)
        totales_frame.grid_columnconfigure(1, weight=0)
        totales_frame.grid_columnconfigure(2, weight=0)

    # ==========================================
    #               LÓGICA
    # ==========================================

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

    # ---------- RENDER VISUAL DE LA LISTA ----------

    def _render_lista_resultados(self):
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
            ) = p
            precio_float = self._parse_precio(precio)
            marker = "✔" if pid == selected_id else " "
            line = (
                f"[{marker}] ID:{pid} | {nombre} | "
                f"P.Venta RD$ {precio_float:.2f} | Stock: {stock} | CB:{cb}\n"
            )
            self.lista_resultados.insert("end", line)

    # ----------------- BÚSQUEDA --------------------

    def buscar_producto(self):
        texto = self.entry_buscar.get().strip()

        if not texto:
            messagebox.showerror("Error", "Introduce código de barras, ID o nombre.")
            return

        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Intentar usar texto como ID entero
        try:
            id_interno = int(texto)
        except ValueError:
            id_interno = -1  # ID que no va a existir

        cursor.execute(
            """
            SELECT id, nombre, precio, precio_base, precio_minimo,
                   stock, codigo_barras, imagen_path
            FROM productos
            WHERE activo = 1
              AND (
                    codigo_barras = ?
                 OR id = ?
                 OR nombre LIKE ?
              )
            ORDER BY nombre
            LIMIT 20
            """,
            (texto, id_interno, f"%{texto}%")
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
                self.product_info_label.configure(text="Producto seleccionado: ninguno")
                self.product_image_label.configure(text="Sin imagen", image=None)
            except Exception:
                pass
            self.product_ctk_image = None
            self.product_image_label.image = None
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
        ) = producto_row
        precio_float = self._parse_precio(precio)

        try:
            self.product_info_label.configure(
                text=(
                    f"{nombre} | P.Venta: RD$ {precio_float:.2f} | "
                    f"Stock: {stock} | CB: {cb}"
                )
            )
        except Exception:
            pass

        if imagen_path and os.path.exists(imagen_path):
            try:
                img = Image.open(imagen_path)
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
                        image=None
                    )
                except Exception:
                    pass
                self.product_image_label.image = None
        else:
            self.product_ctk_image = None
            try:
                self.product_image_label.configure(text="Sin imagen", image=None)
            except Exception:
                pass
            self.product_image_label.image = None

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

        cantidad_texto = self.entry_cantidad.get().strip()
        if not cantidad_texto:
            messagebox.showerror("Error", "Introduce una cantidad.")
            return

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
        ) = self.producto_actual

        precio_unit = self._parse_precio(precio)

        if cantidad > stock:
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

        item = {
            "id": pid,
            "nombre": nombre,
            "cantidad": cantidad,
            "precio": precio_unit,
            "descuento": descuento_linea,
            "subtotal_bruto": subtotal_bruto,
            "subtotal_neto": subtotal_neto,
        }

        self.factura_items.append(item)
        self._recalcular_totales_y_refrescar()

        self.entry_cantidad.delete(0, "end")
        self.entry_desc_pct.delete(0, "end")
        self.entry_desc_monto.delete(0, "end")

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
            total = subt  # sin ITBIS

            self.tree_factura.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    item["nombre"],
                    f"{cant:.2f}",
                    f"{precio:.2f}",
                    f"{desc:.2f}",
                    f"{subt:.2f}",
                    f"{total:.2f}",
                )
            )

    def _recalcular_totales_y_refrescar(self):
        if not self.factura_items:
            self.subtotal_bruto = 0.0
            self.descuentos_items_total = 0.0
            self.descuento_global_monto = 0.0
            self.subtotal_total = 0.0
            self.impuestos_total = 0.0
            self.total_factura = 0.0

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

        self.subtotal_total = self.subtotal_bruto - descuento_total
        self.impuestos_total = 0.0
        self.total_factura = self.subtotal_total

        self._refrescar_tree_factura()
        self._refrescar_totales_ui(descuento_total)

    def _refrescar_totales_ui(self, descuento_total):
        self.lbl_subtotal.configure(
            text=f"Subtotal: RD$ {self.subtotal_total:.2f}"
        )
        self.lbl_descuentos.configure(
            text=f"Descuentos: RD$ {descuento_total:.2f}"
        )
        self.lbl_total.configure(
            text=f"Total: RD$ {self.total_factura:.2f}"
        )

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
                SELECT precio_base, precio_minimo
                FROM productos
                WHERE id = ?
                """,
                (item["id"],)
            )
            row = cursor.fetchone()
            conn.close()

            precio_minimo = row[1] if row and row[1] is not None else nuevo_prec

            subtotal_bruto = nuevo_prec * nueva_cant

            max_desc_por_min = subtotal_bruto - (precio_minimo * nueva_cant)
            if max_desc_por_min < 0:
                max_desc_por_min = 0.0

            if nuevo_desc > max_desc_por_min:
                nuevo_desc = max_desc_por_min

            subtotal_neto = subtotal_bruto - nuevo_desc

            item["cantidad"] = nueva_cant
            item["precio"] = nuevo_prec
            item["descuento"] = nuevo_desc
            item["subtotal_bruto"] = subtotal_bruto
            item["subtotal_neto"] = subtotal_neto

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

        self._mostrar_dialogo_pago()

    def _mostrar_dialogo_pago(self):
        """
        Diálogo para ingresar pagos (efectivo, tarjeta, transferencia),
        calcular cambio y luego llamar a _guardar_y_imprimir_factura().
        """
        total = self.total_factura

        dlg = ctk.CTkToplevel(self.parent)
        dlg.title("Pago de factura")
        dlg.geometry("380x260")
        dlg.resizable(False, False)

        ctk.CTkLabel(
            dlg,
            text=f"Total a pagar: RD$ {total:.2f}",
            font=("Arial", 14, "bold")
        ).pack(pady=(10, 10))

        # ---- EFECTIVO ----
        frame_ef = ctk.CTkFrame(dlg)
        frame_ef.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(frame_ef, text="Efectivo:").pack(side="left", padx=5)
        entry_ef = ctk.CTkEntry(frame_ef, width=120)
        entry_ef.pack(side="left", padx=5)
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

        # ---- CAMBIO ----
        lbl_cambio = ctk.CTkLabel(
            dlg,
            text="Cambio: RD$ 0.00",
            font=("Arial", 13, "bold")
        )
        lbl_cambio.pack(pady=(5, 5))

        def recalcular_cambio(*args):
            try:
                ef = float(entry_ef.get() or "0")
                tar = float(entry_tar.get() or "0")
                trf = float(entry_tr.get() or "0")
            except ValueError:
                lbl_cambio.configure(text="Cambio: RD$ 0.00")
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
                ef = float(entry_ef.get() or "0")
                tar = float(entry_tar.get() or "0")
                trf = float(entry_tr.get() or "0")
            except ValueError:
                messagebox.showerror("Error", "Montos de pago inválidos.")
                return

            if ef < 0 or tar < 0 or trf < 0:
                messagebox.showerror("Error", "Los montos deben ser positivos.")
                return

            total_pagos = ef + tar + trf
            if total_pagos < total - 0.01:
                messagebox.showerror(
                    "Pago insuficiente",
                    f"El total de pagos RD$ {total_pagos:.2f} es menor que el total RD$ {total:.2f}"
                )
                return

            pagos = []
            if ef > 0:
                pagos.append({"tipo": "efectivo", "monto": ef})
            if tar > 0:
                pagos.append({"tipo": "tarjeta", "monto": tar})
            if trf > 0:
                pagos.append({"tipo": "transferencia", "monto": trf})

            dlg.destroy()
            self._guardar_y_imprimir_factura(pagos)

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

    def _build_ticket_text(self, numero, fecha, usuario,
                           detalles, subtotal_bruto,
                           descuento_total, total):
        """
        Construye el texto del ticket ajustándose al ancho configurable.
        """
        ticket_width = self.db.get_ticket_width()  # ancho dinámico
        lines = []

        def center(text):
            return text.center(ticket_width)

        def sep(char="-"):
            return char * ticket_width

        # -----------------------------------
        # ENCABEZADO
        # -----------------------------------
        lines.append(center("ESQUINA TROPICAL"))
        lines.append(center("RNC: N/A"))
        lines.append(center("Tel: N/A"))
        lines.append(sep())
        lines.append(f"Factura: {numero}")
        lines.append(f"Fecha : {fecha}")
        if usuario:
            lines.append(f"Cajero: {usuario}")
        lines.append(sep())

        # -----------------------------------
        # DETALLES
        # -----------------------------------
        lines.append("DESCRIPCIÓN")
        header_left = "CANT x P.U"
        header_right = "IMPORTE"
        spaces_hdr = ticket_width - len(header_left) - len(header_right)
        if spaces_hdr < 1:
            spaces_hdr = 1
        lines.append(header_left + " " * spaces_hdr + header_right)
        lines.append(sep())

        for desc, cant, pu, total_linea in detalles:
            desc = str(desc)
            # cortar descripción si excede el ancho
            while len(desc) > ticket_width:
                lines.append(desc[:ticket_width])
                desc = desc[ticket_width:]
            if desc:
                lines.append(desc)

            left = f"{cant:.2f} x {pu:.2f}"
            right = f"{total_linea:.2f}"

            spaces = ticket_width - len(left) - len(right)
            if spaces < 1:
                spaces = 1

            lines.append(left + " " * spaces + right)

        # -----------------------------------
        # TOTALES
        # -----------------------------------
        lines.append(sep())
        lines.append(f"SUBTOTAL:".ljust(ticket_width - 10) + f"{subtotal_bruto:10.2f}")
        lines.append(f"DESCUENTO:".ljust(ticket_width - 10) + f"{descuento_total:10.2f}")
        lines.append(f"TOTAL:".ljust(ticket_width - 10) + f"{total:10.2f}")
        lines.append(sep())
        lines.append(center("GRACIAS POR SU COMPRA"))
        lines.append("\n\n\n")

        return "\n".join(lines)

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

    def _guardar_y_imprimir_factura(self, pagos):
        numero = self._generar_numero_factura()

        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()

            descuento_total = self.descuentos_items_total + self.descuento_global_monto

            # Guardar encabezado
            cursor.execute(
                """
                INSERT INTO facturas
                    (numero, tipo_comprobante, cliente_id, subtotal,
                     descuento_total, impuesto_total, total, estado, usuario, caja)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    numero,
                    "consumidor_final",
                    None,
                    round(self.subtotal_total, 2),
                    round(descuento_total, 2),
                    0.0,  # no usas ITBIS
                    round(self.total_factura, 2),
                    "emitida",
                    self.current_user,
                    None
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
                        0.0,
                        round(item["subtotal_neto"], 2)
                    )
                )

                # actualizar stock
                cursor.execute(
                    "UPDATE productos SET stock = stock - ? WHERE id = ?",
                    (item["cantidad"], item["id"])
                )

                cursor.execute(
                    "SELECT stock FROM productos WHERE id = ?",
                    (item["id"],)
                )
                stk = cursor.fetchone()[0]
                if stk <= 10:
                    low_stock.append((item["nombre"], stk))

                # agregar al ticket
                detalles_ticket.append(
                    (
                        item["nombre"],
                        item["cantidad"],
                        item["precio"],
                        item["subtotal_neto"]
                    )
                )

            # Guardar pagos
            for p in pagos:
                cursor.execute(
                    """
                    INSERT INTO pagos_factura (factura_id, tipo_pago, monto)
                    VALUES (?, ?, ?)
                    """,
                    (factura_id, p["tipo"], round(p["monto"], 2))
                )

            # Fecha real guardada
            cursor.execute("SELECT fecha FROM facturas WHERE id = ?", (factura_id,))
            row_fecha = cursor.fetchone()
            fecha_txt = row_fecha[0] if row_fecha else datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            conn.commit()
            conn.close()

            # Avisos
            if low_stock:
                msg = "Productos con stock bajo:\n\n"
                for n, s in low_stock:
                    msg += f"• {n} → {s}\n"
                messagebox.showwarning("Stock bajo", msg)

            # -----------------------------
            # GENERAR TICKET DINÁMICO
            # -----------------------------
            ticket_text = self._build_ticket_text(
                numero,
                fecha_txt,
                self.current_user,
                detalles_ticket,
                self.subtotal_bruto,
                descuento_total,
                self.total_factura
            )

            # Guardar ticket en carpeta 'facturas'
            try:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                facturas_dir = os.path.join(base_dir, "facturas")
                os.makedirs(facturas_dir, exist_ok=True)

                ticket_path = os.path.join(facturas_dir, f"{numero}.txt")
                with open(ticket_path, "w", encoding="utf-8") as f:
                    f.write(ticket_text)
            except Exception as e:
                print("Error guardando ticket en carpeta 'facturas':", e)

            # ✅ Enviar a la impresora (RAW/ESC-POS)
            self._send_to_printer(ticket_text)

            # limpiar la factura en pantalla
            self.factura_items.clear()
            self._recalcular_totales_y_refrescar()

        except Exception as e:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
            messagebox.showerror("Error", f"No se pudo guardar/imprimir la factura:\n{e}")
