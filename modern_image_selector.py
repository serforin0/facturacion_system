import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw
import os

class ModernImageSelector(ctk.CTkFrame):
    def __init__(self, master, image_manager, **kwargs):
        super().__init__(master, **kwargs)
        self.image_manager = image_manager
        self.current_image_path = None
        self.preview_image = None
        self.setup_ui()
    
    def setup_ui(self):
        # Configurar el frame principal
        self.configure(fg_color="#2a2d2e", corner_radius=10)
        
        # Frame para la zona de imagen
        self.image_frame = ctk.CTkFrame(
            self, 
            fg_color="#3B3B3B", 
            corner_radius=10,
            border_width=2,
            border_color="#4A4A4A",
            height=200
        )
        self.image_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Efectos hover
        self.image_frame.bind("<Enter>", self.on_hover_enter)
        self.image_frame.bind("<Leave>", self.on_hover_leave)
        
        # Label para la imagen preview
        self.image_label = ctk.CTkLabel(
            self.image_frame, 
            text="", 
            width=120, 
            height=120,
            fg_color="transparent"
        )
        self.image_label.pack(pady=(20, 10))
        
        # Texto instructivo
        self.info_label = ctk.CTkLabel(
            self.image_frame, 
            text="🖼️ HACER CLIC PARA\nSELECCIONAR IMAGEN",
            text_color="lightblue",
            font=("Arial", 12, "bold"),
            wraplength=150
        )
        self.info_label.pack(pady=(0, 10))
        
        # Botón para seleccionar imagen
        self.select_btn = ctk.CTkButton(
            self.image_frame,
            text="📁 SELECCIONAR IMAGEN",
            command=self.select_image,
            fg_color="#2B5F87",
            width=140,
            height=35,
            font=("Arial", 12, "bold")
        )
        self.select_btn.pack(pady=(0, 10))
        
        # Botón para eliminar imagen
        self.clear_btn = ctk.CTkButton(
            self.image_frame,
            text="❌ ELIMINAR IMAGEN",
            command=self.clear_image,
            fg_color="transparent",
            border_width=2,
            border_color="#FF4444",
            text_color="#FF4444",
            width=120,
            height=30,
            font=("Arial", 11, "bold")
        )
        self.clear_btn.pack(pady=(0, 10))
        self.clear_btn.pack_forget()  # Ocultar inicialmente
        
        # Hacer el frame clickeable
        self.image_frame.bind("<Button-1>", lambda e: self.select_image())
        self.image_label.bind("<Button-1>", lambda e: self.select_image())
        self.info_label.bind("<Button-1>", lambda e: self.select_image())
        
        # Mostrar imagen por defecto
        self.show_default_image()
    
    def on_hover_enter(self, event):
        """Efecto cuando el mouse entra"""
        self.image_frame.configure(border_color="#2B5F87")
    
    def on_hover_leave(self, event):
        """Efecto cuando el mouse sale"""
        self.image_frame.configure(border_color="#4A4A4A")
    
    def select_image(self):
        """Abrir diálogo para seleccionar imagen"""
        file_path = filedialog.askopenfilename(
            title="Seleccionar imagen del producto",
            filetypes=[
                ("Imágenes", "*.jpg *.jpeg *.png *.gif *.bmp *.webp"),
                ("Todos los archivos", "*.*")
            ]
        )
        if file_path:
            self.load_image_from_path(file_path)
    
    def load_image_from_path(self, file_path):
        """Cargar imagen desde ruta y mostrar preview"""
        try:
            # Validar que sea una imagen
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
            file_ext = os.path.splitext(file_path)[1].lower()
            
            if file_ext not in valid_extensions:
                messagebox.showerror("Error", f"Formato {file_ext} no válido. Use JPG, PNG, GIF, BMP o WEBP.")
                return
            
            # Cargar y redimensionar imagen
            image = Image.open(file_path)
            image.thumbnail((120, 120), Image.Resampling.LANCZOS)
            
            # Convertir para CTk
            self.preview_image = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self.preview_image, text="")
            
            # Guardar ruta temporal
            self.current_image_path = file_path
            
            # Actualizar UI
            self.info_label.configure(
                text="✅ IMAGEN CARGADA\nHacer clic para cambiar",
                text_color="#4CAF50"
            )
            self.clear_btn.pack(pady=(0, 10))  # Mostrar botón eliminar
            
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar la imagen: {str(e)}")
    
    def clear_image(self):
        """Eliminar imagen actual"""
        self.current_image_path = None
        self.preview_image = None
        self.show_default_image()
        self.clear_btn.pack_forget()  # Ocultar botón eliminar
    
    def show_default_image(self):
        """Mostrar imagen por defecto"""
        default_image = self.image_manager.get_default_image((100, 100))
        self.image_label.configure(image=default_image, text="")
        self.info_label.configure(
            text="🖼️ HACER CLIC PARA\nSELECCIONAR IMAGEN",
            text_color="lightblue"
        )
    
    def load_existing_image(self, image_path):
        """Cargar una imagen existente en el selector"""
        if image_path and os.path.exists(image_path):
            try:
                image = Image.open(image_path)
                self.display_image(image)
                self.current_image_path = image_path
            except Exception as e:
                print(f"Error cargando imagen existente: {e}")
    
    def get_image_path(self):
        """Obtener la ruta de la imagen seleccionada"""
        return self.current_image_path
    
    def set_image_from_product(self, product_id):
        """Cargar imagen existente de un producto"""
        if product_id:
            image_path = self.image_manager.get_image_path(product_id)
            if image_path:
                self.load_image_from_path(image_path)
            else:
                self.show_default_image()
        else:
            self.show_default_image()