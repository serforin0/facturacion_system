import customtkinter as ctk
from tkinter import ttk, messagebox
from database import Database
from styles import Styles
from image_manager import ImageManager
from drag_drop_frame import DragDropImageFrame
from modern_image_selector import ModernImageSelector
import tkinter as tk
from PIL import Image, ImageTk
import os

class InventoryManager:
    def __init__(self, parent_frame):
        self.db = Database()
        self.image_manager = ImageManager()
        self.parent = parent_frame
        self.setup_ui()
        self.load_products()
    
    def setup_ui(self):
        # Frame principal
        self.main_frame = ctk.CTkFrame(self.parent, **Styles.get_frame_style())
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Título
        title = ctk.CTkLabel(
            self.main_frame, 
            text="🎯 GESTIÓN DE INVENTARIO", 
            font=("Arial", 20, "bold")
        )
        title.pack(pady=10)
        
        # Frame de controles (agregar producto)
        self.setup_controls_frame()
        
        # Frame de lista de productos
        self.setup_products_frame()

    def setup_controls_frame(self):
        controls_frame = ctk.CTkFrame(self.main_frame, **Styles.get_frame_style())
        controls_frame.pack(fill="x", padx=10, pady=10)
        
        # Título de controles
        ctk.CTkLabel(controls_frame, text="Agregar Nuevo Producto", 
                    font=("Arial", 16, "bold")).pack(pady=5)
        
        # Frame principal del formulario (ahora con imagen)
        main_form_frame = ctk.CTkFrame(controls_frame)
        main_form_frame.pack(fill="x", padx=10, pady=5)
        
        # COLUMNA IZQUIERDA - Formulario de texto
        left_frame = ctk.CTkFrame(main_form_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=5)
        
        # Formulario en 2 columnas dentro de left_frame
        form_frame = ctk.CTkFrame(left_frame)
        form_frame.pack(fill="x", padx=5, pady=5)
        
        # Columna 1 del formulario
        col1 = ctk.CTkFrame(form_frame)
        col1.pack(side="left", fill="x", expand=True, padx=2)
        
        # Nombre
        ctk.CTkLabel(col1, text="Nombre:").pack(anchor="w")
        self.name_entry = ctk.CTkEntry(col1, placeholder_text="Ej: Corona Extra")
        self.name_entry.pack(fill="x", pady=2)
        
        # Descripción
        ctk.CTkLabel(col1, text="Descripción:").pack(anchor="w")
        self.desc_entry = ctk.CTkEntry(col1, placeholder_text="Ej: Cerveza clara 355ml")
        self.desc_entry.pack(fill="x", pady=2)
        
        # Columna 2 del formulario
        col2 = ctk.CTkFrame(form_frame)
        col2.pack(side="left", fill="x", expand=True, padx=2)
        
        # Precio
        ctk.CTkLabel(col2, text="Precio ($):").pack(anchor="w")
        self.price_entry = ctk.CTkEntry(col2, placeholder_text="Ej: 25.00")
        self.price_entry.pack(fill="x", pady=2)
        
        # Stock
        ctk.CTkLabel(col2, text="Stock Inicial:").pack(anchor="w")
        self.stock_entry = ctk.CTkEntry(col2, placeholder_text="Ej: 50")
        self.stock_entry.pack(fill="x", pady=2)
        
        # Columna 3 del formulario
        col3 = ctk.CTkFrame(form_frame)
        col3.pack(side="left", fill="x", expand=True, padx=2)
        
        # Categoría
        ctk.CTkLabel(col3, text="Categoría:").pack(anchor="w")
        self.category_combo = ctk.CTkComboBox(col3, values=self.get_categories())
        self.category_combo.pack(fill="x", pady=2)
        
        # Stock mínimo
        ctk.CTkLabel(col3, text="Stock Mínimo:").pack(anchor="w")
        self.min_stock_entry = ctk.CTkEntry(col3, placeholder_text="Ej: 5")
        self.min_stock_entry.pack(fill="x", pady=2)
        
        # COLUMNA DERECHA - Selector de imagen
        right_frame = ctk.CTkFrame(main_form_frame, width=220)
        right_frame.pack(side="right", fill="y", padx=5)
        right_frame.pack_propagate(False)
        
        ctk.CTkLabel(right_frame, text="Imagen del Producto", 
                    font=("Arial", 14, "bold")).pack(pady=5)
        
        # Frame de arrastrar y soltar moderno
        self.image_selector = ModernImageSelector(right_frame, self.image_manager)
        self.image_selector.pack(padx=5, pady=5, fill="both", expand=True)

        # Código de barras
        ctk.CTkLabel(col1, text="Código de Barras:").pack(anchor="w")
        self.barcode_entry = ctk.CTkEntry(col1, placeholder_text="Escanea aquí o escribe el código")
        self.barcode_entry.pack(fill="x", pady=2)

        
        # BOTONES - TODOS EN LA MISMA FILA (4 BOTONES)
        button_frame = ctk.CTkFrame(controls_frame)
        button_frame.pack(fill="x", padx=10, pady=10)
        
        # Configurar grid para 4 columnas iguales
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)
        
        # Botón Agregar
        ctk.CTkButton(
            button_frame, 
            text="➕ AGREGAR", 
            command=self.add_product,
            fg_color=Styles.SECONDARY,
            height=35,
            font=("Arial", 12, "bold")
        ).grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        
        # Botón Actualizar
        ctk.CTkButton(
            button_frame, 
            text="🔄 ACTUALIZAR", 
            command=self.load_products,
            fg_color=Styles.PRIMARY,
            height=35,
            font=("Arial", 12, "bold")
        ).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        # Botón Editar
        ctk.CTkButton(
            button_frame, 
            text="✏️ EDITAR", 
            command=self.edit_product,
            fg_color="#FFA500",  # Naranja
            height=35,
            font=("Arial", 12, "bold")
        ).grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        
        # Botón Eliminar
        ctk.CTkButton(
            button_frame, 
            text="🗑️ ELIMINAR", 
            command=self.delete_product,
            fg_color=Styles.DANGER,
            height=35,
            font=("Arial", 12, "bold")
        ).grid(row=0, column=3, padx=5, pady=5, sticky="ew")

    def setup_products_frame(self):
        # Frame para la lista de productos
        products_frame = ctk.CTkFrame(self.main_frame, **Styles.get_frame_style())
        products_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Título
        ctk.CTkLabel(products_frame, text="📦 LISTA DE PRODUCTOS", 
                    font=("Arial", 16, "bold")).pack(pady=5)
        
        # Treeview para mostrar productos
        tree_frame = ctk.CTkFrame(products_frame)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Crear Treeview con estilo
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2a2d2e", foreground="white", 
                    rowheight=25, fieldbackground="#2a2d2e", borderwidth=0)
        style.configure("Treeview.Heading", background="#3B3B3B", foreground="white", 
                    relief="flat", font=("Arial", 12, "bold"))
        style.map('Treeview', background=[('selected', '#22559b')])
        
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("ID", "Nombre", "Descripción", "Precio", "Stock", "Categoría", "Stock Mín", "Código"),
            show="headings",
            height=15
        )
        
        # Configurar columnas
        columns = [
            ("ID", 50),
            ("Nombre", 150),
            ("Descripción", 200),
            ("Precio", 80),
            ("Stock", 80),
            ("Categoría", 100),
            ("Stock Mín", 80),
            ("Código", 120)
        ]
        
        for col, width in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="center")
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        
        self.tree.pack(side="left", fill="both", expand=True)
        
        # Frame para el botón de reporte (opcional, debajo de la tabla)
        report_frame = ctk.CTkFrame(products_frame)
        report_frame.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(
            report_frame, 
            text="📊 VER REPORTE DE STOCK BAJO", 
            command=self.low_stock_report,
            fg_color=Styles.WARNING,
            height=35,
            font=("Arial", 12, "bold")
        ).pack(pady=5)

    def get_categories(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT nombre FROM categorias ORDER BY nombre")
        categories = [row[0] for row in cursor.fetchall()]
        conn.close()
        return categories
    
    def add_product(self):
        # Validar campos
        if not self.validate_fields():
            return
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # Verificar si la columna imagen_path existe
            cursor.execute("PRAGMA table_info(productos)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # Obtener ID de la categoría
            cursor.execute("SELECT id FROM categorias WHERE nombre = ?", 
                         (self.category_combo.get(),))
            categoria_id = cursor.fetchone()[0]
            
            # Insertar producto
            cursor.execute('''
                INSERT INTO productos (nombre, descripcion, precio, stock, categoria_id, stock_minimo, codigo_barras)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                self.name_entry.get(),
                self.desc_entry.get(),
                float(self.price_entry.get()),
                int(self.stock_entry.get()),
                categoria_id,
                int(self.min_stock_entry.get() or 5),
                self.barcode_entry.get().strip() or None
            ))
            
            # Obtener el ID del producto recién insertado
            product_id = cursor.lastrowid
            
            # Manejar la imagen si existe
            image_path = self.image_selector.get_image_path()
            if image_path:
                # Copiar imagen al directorio de la app
                final_image_path = self.image_manager.copy_image_to_app(image_path, product_id)
                if final_image_path:
                    # Verificar si la columna existe
                    if 'imagen_path' not in columns:
                        # Si no existe, crear la columna
                        cursor.execute('ALTER TABLE productos ADD COLUMN imagen_path TEXT')
                    
                    # Actualizar producto con la ruta de la imagen
                    cursor.execute('''
                        UPDATE productos SET imagen_path = ? WHERE id = ?
                    ''', (final_image_path, product_id))
            
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
            (self.price_entry, "Precio"),
            (self.stock_entry, "Stock")
        ]
        
        for field, name in required_fields:
            if not field.get().strip():
                messagebox.showwarning("Validación", f"El campo {name} es requerido")
                field.focus()
                return False
        
        try:
            float(self.price_entry.get())
            int(self.stock_entry.get())
            if self.min_stock_entry.get():
                int(self.min_stock_entry.get())
        except ValueError:
            messagebox.showwarning("Validación", "Precio y Stock deben ser números válidos")
            return False
        
        return True
    
    def clear_form(self):
        self.name_entry.delete(0, 'end')
        self.desc_entry.delete(0, 'end')
        self.price_entry.delete(0, 'end')
        self.stock_entry.delete(0, 'end')
        self.min_stock_entry.delete(0, 'end')
        self.barcode_entry.delete(0, 'end') 
        self.category_combo.set("")
        self.image_selector.clear_image()
    
    def load_products(self):
        # Limpiar treeview
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # Verificar si la columna imagen_path existe
        cursor.execute("PRAGMA table_info(productos)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'imagen_path' in columns:
            cursor.execute('''
                SELECT p.id, p.nombre, p.descripcion, p.precio, p.stock, 
                    c.nombre, p.stock_minimo, p.codigo_barras, p.imagen_path
                FROM productos p
                LEFT JOIN categorias c ON p.categoria_id = c.id
                WHERE p.activo = 1
                ORDER BY p.nombre
            ''')
        else:
            # Si no existe, usar consulta sin imagen_path
            cursor.execute('''
                SELECT p.id, p.nombre, p.descripcion, p.precio, p.stock, 
                    c.nombre, p.stock_minimo, p.codigo_barras
                FROM productos p
                LEFT JOIN categorias c ON p.categoria_id = c.id
                WHERE p.activo = 1
                ORDER BY p.nombre
            ''')
        
        for row in cursor.fetchall():
            # Formatear precio con RD$ - manejar diferentes formatos
            precio = row[3]
            if isinstance(precio, str) and "RD$" in precio:
                precio_formateado = precio
            else:
                precio_formateado = f"RD$ {float(precio):.2f}"
            
            row_formatted = list(row)
            row_formatted[3] = precio_formateado
            
            # Resaltar stock bajo
            tags = ('low_stock',) if row[4] <= row[6] else ()
            self.tree.insert("", "end", values=row_formatted, tags=tags)
        
        # Configurar estilo para stock bajo
        self.tree.tag_configure('low_stock', background='#ff6b6b')
        
        conn.close()
    
    def edit_product(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selección", "Por favor selecciona un producto para editar")
            return
        
        # Obtener datos del producto seleccionado
        product_data = self.tree.item(selected[0])['values']
        product_id = product_data[0]
        
        # Crear ventana de edición
        self.create_edit_window(product_id, product_data)
    
    def create_edit_window(self, product_id, product_data):
        """Crear ventana para editar producto - Versión con tkinter estándar y selector de imagen"""
        try:
            # Crear ventana con tkinter estándar
            edit_window = tk.Toplevel(self.parent)
            edit_window.title(f"Editar Producto - ID: {product_id}")
            edit_window.geometry("700x600")
            edit_window.transient(self.parent)
            edit_window.grab_set()
            edit_window.configure(bg='#2b2b2b')
            edit_window.resizable(False, False)
            
            # Centrar la ventana
            edit_window.update_idletasks()
            x = (edit_window.winfo_screenwidth() // 2) - (700 // 2)
            y = (edit_window.winfo_screenheight() // 2) - (600 // 2)
            edit_window.geometry(f"700x600+{x}+{y}")
            
            # Cargar datos actuales del producto
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # Verificar si la columna imagen_path existe
            cursor.execute("PRAGMA table_info(productos)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'imagen_path' in columns:
                cursor.execute('''
                    SELECT p.*, c.nombre as categoria_nombre 
                    FROM productos p 
                    LEFT JOIN categorias c ON p.categoria_id = c.id 
                    WHERE p.id = ?
                ''', (product_id,))
            else:
                cursor.execute('''
                    SELECT p.id, p.nombre, p.descripcion, p.precio, p.stock, 
                           p.categoria_id, p.stock_minimo, p.activo,
                           c.nombre as categoria_nombre 
                    FROM productos p 
                    LEFT JOIN categorias c ON p.categoria_id = c.id 
                    WHERE p.id = ?
                ''', (product_id,))
            
            product = cursor.fetchone()
            conn.close()
            
            # Variable para almacenar la nueva imagen
            self.edit_image_path = None
            
            # Título
            title_label = tk.Label(edit_window, text=f"EDITAR PRODUCTO: {product_data[1]}", 
                                 font=("Arial", 14, "bold"), fg="white", bg='#2b2b2b')
            title_label.pack(pady=15)
            
            # Frame principal con dos columnas
            main_frame = tk.Frame(edit_window, bg='#2b2b2b')
            main_frame.pack(fill="both", expand=True, padx=20, pady=10)
            
            # Columna izquierda - Formulario
            left_frame = tk.Frame(main_frame, bg='#2b2b2b')
            left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
            
            # Columna derecha - Imagen
            right_frame = tk.Frame(main_frame, bg='#2b2b2b', width=200)
            right_frame.pack(side="right", fill="y", padx=(10, 0))
            right_frame.pack_propagate(False)
            
            # FORMULARIO (columna izquierda)
            form_frame = tk.Frame(left_frame, bg='#2b2b2b')
            form_frame.pack(fill="both", expand=True)
            
            # Nombre
            tk.Label(form_frame, text="Nombre:", fg="white", bg='#2b2b2b', 
                    font=("Arial", 10, "bold")).pack(anchor="w", pady=(10, 5))
            name_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            name_entry.insert(0, product_data[1])
            name_entry.pack(fill="x", pady=(0, 15))
            
            # Descripción
            tk.Label(form_frame, text="Descripción:", fg="white", bg='#2b2b2b',
                    font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 5))
            desc_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            desc_entry.insert(0, product_data[2] if product_data[2] else "")
            desc_entry.pack(fill="x", pady=(0, 15))
            
            # Precio
            tk.Label(form_frame, text="Precio ($):", fg="white", bg='#2b2b2b',
                    font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 5))
            price_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            precio_limpio = product_data[3].replace("RD$ ", "") if "RD$" in str(product_data[3]) else product_data[3]
            price_entry.insert(0, str(precio_limpio))
            price_entry.pack(fill="x", pady=(0, 15))
            
            # Stock
            tk.Label(form_frame, text="Stock:", fg="white", bg='#2b2b2b',
                    font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 5))
            stock_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            stock_entry.insert(0, str(product_data[4]))
            stock_entry.pack(fill="x", pady=(0, 15))
            
            # Categoría
            tk.Label(form_frame, text="Categoría:", fg="white", bg='#2b2b2b',
                    font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 5))
            
            category_frame = tk.Frame(form_frame, bg='#2b2b2b')
            category_frame.pack(fill="x", pady=(0, 15))
            
            categories = self.get_categories()
            category_var = tk.StringVar(edit_window)
            
            if len(product) > 7:
                category_var.set(product[7])  # nombre de la categoría cuando hay imagen_path
            else:
                category_var.set(product[5])  # nombre de la categoría cuando no hay imagen_path
            
            category_dropdown = tk.OptionMenu(category_frame, category_var, *categories)
            category_dropdown.config(width=32, font=("Arial", 10))
            category_dropdown.pack(fill="x")
            
            # Stock Mínimo
            tk.Label(form_frame, text="Stock Mínimo:", fg="white", bg='#2b2b2b',
                    font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 5))
            min_stock_entry = tk.Entry(form_frame, width=35, font=("Arial", 10))
            if len(product) > 6:
                min_stock_entry.insert(0, str(product[6]))
            else:
                min_stock_entry.insert(0, str(product[6] if len(product) > 6 else 5))
            min_stock_entry.pack(fill="x", pady=(0, 15))
            
            # SELECTOR DE IMAGEN (columna derecha)
            tk.Label(right_frame, text="Imagen del Producto", fg="white", bg='#2b2b2b',
                    font=("Arial", 12, "bold")).pack(pady=(10, 5))
            
            # Frame para la imagen
            image_frame = tk.Frame(right_frame, bg='#3a3a3a', relief='sunken', bd=1)
            image_frame.pack(fill="both", expand=True, padx=5, pady=5)
            
            # Label para mostrar la imagen
            image_label = tk.Label(image_frame, text="Arrastra imagen aquí\no haz clic para seleccionar", 
                                 bg='#3a3a3a', fg='white', font=("Arial", 10), 
                                 wraplength=180, justify='center')
            image_label.pack(expand=True, fill='both', padx=10, pady=10)
            
            # Botón para seleccionar imagen
            def select_image():
                from tkinter import filedialog
                file_path = filedialog.askopenfilename(
                    title="Seleccionar imagen",
                    filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.gif *.bmp")]
                )
                if file_path:
                    self.edit_image_path = file_path
                    display_image(file_path)
            
            def display_image(image_path):
                try:
                    image = Image.open(image_path)
                    # Redimensionar si es muy grande
                    if image.width > 180 or image.height > 150:
                        image.thumbnail((180, 150), Image.Resampling.LANCZOS)
                    
                    photo = ImageTk.PhotoImage(image)
                    image_label.configure(image=photo, text="")
                    image_label.image = photo  # Mantener referencia
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo cargar la imagen: {str(e)}")
            
            # Cargar imagen actual si existe
            current_image_path = None
            if len(product) > 8 and product[8]:  # imagen_path cuando existe la columna
                current_image_path = product[8]
                if os.path.exists(current_image_path):
                    display_image(current_image_path)
                    self.edit_image_path = current_image_path
            
            # Botón para seleccionar imagen
            select_btn = tk.Button(right_frame, text="📁 Seleccionar Imagen", 
                                 command=select_image, bg='#1f6aa5', fg='white',
                                 font=("Arial", 10, "bold"), width=15)
            select_btn.pack(pady=10)
            
            # Botón para eliminar imagen
            def clear_image():
                self.edit_image_path = None
                image_label.configure(image="", text="Arrastra imagen aquí\no haz clic para seleccionar")
            
            clear_btn = tk.Button(right_frame, text="🗑️ Eliminar Imagen", 
                                command=clear_image, bg='#d9534f', fg='white',
                                font=("Arial", 10, "bold"), width=15)
            clear_btn.pack(pady=5)
            
            # Botones principales
            button_frame = tk.Frame(main_frame, bg='#2b2b2b')
            button_frame.pack(fill="x", pady=20)
            
            def guardar_cambios():
                try:
                    # Validar campos
                    if not name_entry.get().strip():
                        messagebox.showerror("Error", "El campo Nombre es requerido")
                        name_entry.focus()
                        return
                    
                    if not price_entry.get().strip():
                        messagebox.showerror("Error", "El campo Precio es requerido")
                        price_entry.focus()
                        return
                    
                    if not stock_entry.get().strip():
                        messagebox.showerror("Error", "El campo Stock es requerido")
                        stock_entry.focus()
                        return
                    
                    # Validar que precio y stock sean números
                    try:
                        precio_valor = float(price_entry.get())
                        stock_valor = int(stock_entry.get())
                    except ValueError:
                        messagebox.showerror("Error", "Precio debe ser un número y Stock un número entero")
                        return
                    
                    # Obtener ID de categoría
                    conn = self.db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM categorias WHERE nombre = ?", (category_var.get(),))
                    result = cursor.fetchone()
                    
                    if not result:
                        messagebox.showerror("Error", "Categoría no válida")
                        return
                    
                    categoria_id = result[0]
                    
                    # Actualizar producto
                    cursor.execute('''
                        UPDATE productos 
                        SET nombre = ?, descripcion = ?, precio = ?, stock = ?, 
                            categoria_id = ?, stock_minimo = ?
                        WHERE id = ?
                    ''', (
                        name_entry.get().strip(),
                        desc_entry.get().strip(),
                        precio_valor,
                        stock_valor,
                        categoria_id,
                        int(min_stock_entry.get() or 5),
                        product_id
                    ))
                    
                    # Manejar la imagen si se seleccionó una nueva
                    if self.edit_image_path and self.edit_image_path != current_image_path:
                        final_image_path = self.image_manager.copy_image_to_app(self.edit_image_path, product_id)
                        if final_image_path:
                            # Verificar si la columna existe antes de actualizar
                            cursor.execute("PRAGMA table_info(productos)")
                            columns = [column[1] for column in cursor.fetchall()]
                            
                            if 'imagen_path' in columns:
                                cursor.execute('''
                                    UPDATE productos SET imagen_path = ? WHERE id = ?
                                ''', (final_image_path, product_id))
                            else:
                                # Si no existe, crear la columna
                                cursor.execute('ALTER TABLE productos ADD COLUMN imagen_path TEXT')
                                cursor.execute('''
                                    UPDATE productos SET imagen_path = ? WHERE id = ?
                                ''', (final_image_path, product_id))
                    
                    conn.commit()
                    conn.close()
                    
                    messagebox.showinfo("Éxito", "Producto actualizado correctamente")
                    edit_window.destroy()
                    self.load_products()  # Refrescar lista
                    
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo actualizar el producto: {str(e)}")
            
            # Botón Guardar
            save_btn = tk.Button(button_frame, text="💾 GUARDAR CAMBIOS", 
                               command=guardar_cambios, bg='#2fa572', fg='white',
                               font=("Arial", 11, "bold"), width=20, height=2)
            save_btn.pack(side="left", padx=10)
            
            # Botón Cancelar
            cancel_btn = tk.Button(button_frame, text="❌ CANCELAR", 
                                 command=edit_window.destroy, bg='#d9534f', fg='white',
                                 font=("Arial", 11, "bold"), width=15, height=2)
            cancel_btn.pack(side="right", padx=10)
            
            # Hacer que la ventana sea modal
            edit_window.focus_set()
            edit_window.wait_window()
            
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir la ventana de edición: {str(e)}")
    
    def delete_product(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selección", "Por favor selecciona un producto para eliminar")
            return
        
        product_id = self.tree.item(selected[0])['values'][0]
        product_name = self.tree.item(selected[0])['values'][1]
        
        # Confirmación más detallada
        confirm = messagebox.askyesno(
            "Confirmar Eliminación", 
            f"¿Estás seguro de eliminar el producto?\n\n"
            f"Producto: {product_name}\n"
            f"ID: {product_id}\n\n"
            f"Esta acción no se puede deshacer."
        )
        
        if confirm:
            try:
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                # Eliminar la imagen asociada si existe
                self.image_manager.delete_product_image(product_id)
                
                # Marcar como inactivo en la base de datos
                cursor.execute("UPDATE productos SET activo = 0 WHERE id = ?", (product_id,))
                conn.commit()
                conn.close()
                
                messagebox.showinfo("Éxito", f"Producto '{product_name}' eliminado correctamente")
                self.load_products()
                
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo eliminar el producto: {str(e)}")
    
    def low_stock_report(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT p.nombre, p.stock, p.stock_minimo, c.nombre
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            WHERE p.stock <= p.stock_minimo AND p.activo = 1
            ORDER BY p.stock ASC
        ''')
        
        low_stock_products = cursor.fetchall()
        conn.close()
        
        if not low_stock_products:
            messagebox.showinfo("Stock", "✅ No hay productos con stock bajo")
            return
        
        # Mostrar reporte
        report = "📊 PRODUCTOS CON STOCK BAJO:\n\n"
        for product in low_stock_products:
            report += f"• {product[0]} ({product[3]})\n"
            report += f"  Stock actual: {product[1]} | Mínimo: {product[2]}\n\n"
        
        messagebox.showwarning("Stock Bajo", report)