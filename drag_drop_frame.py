import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import os

class DragDropImageFrame(ctk.CTkFrame):
    def __init__(self, master, image_manager, **kwargs):
        super().__init__(master, **kwargs)
        self.image_manager = image_manager
        self.current_image_path = None
        self.preview_image = None
        self.setup_ui()
    
    def setup_ui(self):
        # Frame para la zona de arrastrar y soltar
        self.drop_frame = ctk.CTkFrame(self, width=200, height=200, 
                                      fg_color="#3B3B3B", corner_radius=10)
        self.drop_frame.pack(padx=10, pady=10, fill="both", expand=True)
        self.drop_frame.pack_propagate(False)
        
        # Label para la imagen preview
        self.image_label = ctk.CTkLabel(self.drop_frame, text="", width=180, height=180)
        self.image_label.pack(padx=10, pady=10)
        
        # Texto instructivo
        self.info_label = ctk.CTkLabel(
            self.drop_frame, 
            text="🖼️ Arrastra una imagen aquí\no haz clic para seleccionar",
            text_color="lightblue",
            font=("Arial", 12, "bold")
        )
        self.info_label.pack(pady=5)
        
        # Botón para eliminar imagen
        self.clear_btn = ctk.CTkButton(
            self.drop_frame,
            text="❌ Eliminar Imagen",
            command=self.clear_image,
            fg_color="transparent",
            border_width=2,
            border_color="#FF4444",
            text_color="#FF4444",
            width=120,
            height=30
        )
        self.clear_btn.pack(pady=5)
        self.clear_btn.pack_forget()  # Ocultar inicialmente
        
        # Configurar eventos de arrastrar y soltar
        self.setup_drag_drop()
        
        # Mostrar imagen por defecto
        self.show_default_image()
    
    def setup_drag_drop(self):
        # Configurar eventos para el frame
        widgets = [self.drop_frame, self.image_label, self.info_label]
        
        for widget in widgets:
            widget.bind("<Button-1>", self.on_click)
        
        # Hacer que el frame acepte drops (esto funciona en Windows)
        self.drop_frame.bind("<DragEnter>", self.on_drag_enter)
        self.drop_frame.bind("<DragLeave>", self.on_drag_leave)
        self.drop_frame.bind("<Drop>", self.on_drop)
    
    def on_click(self, event):
        """Cuando hacen clic en el área de imagen"""
        file_path = filedialog.askopenfilename(
            title="Seleccionar imagen del producto",
            filetypes=[
                ("Imágenes", "*.jpg *.jpeg *.png *.gif *.bmp"),
                ("Todos los archivos", "*.*")
            ]
        )
        if file_path:
            self.load_image_from_path(file_path)
    
    def on_drag_enter(self, event):
        """Cuando arrastran una imagen sobre el área"""
        self.drop_frame.configure(fg_color="#4A4A4A")
        return True
    
    def on_drag_leave(self, event):
        """Cuando salen del área de drop"""
        self.drop_frame.configure(fg_color="#3B3B3B")
    
    def on_drop(self, event):
        """Cuando sueltan una imagen"""
        self.drop_frame.configure(fg_color="#3B3B3B")
        
        # En Windows, el evento drop tiene los archivos en event.data
        if hasattr(event, 'data'):
            # Limpiar y obtener la ruta
            file_path = event.data.strip('{}')
            if os.path.exists(file_path):
                self.load_image_from_path(file_path)
    
    def load_image_from_path(self, file_path):
        """Cargar imagen desde ruta y mostrar preview"""
        try:
            # Cargar y redimensionar imagen
            image = Image.open(file_path)
            image.thumbnail((150, 150), Image.Resampling.LANCZOS)
            
            # Convertir para CTk
            self.preview_image = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self.preview_image, text="")
            
            # Guardar ruta temporal
            self.current_image_path = file_path
            
            # Actualizar UI
            self.info_label.configure(text="✅ Imagen cargada\nHaz clic para cambiar")
            self.clear_btn.pack(pady=5)
            
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar la imagen: {str(e)}")
    
    def clear_image(self):
        """Eliminar imagen actual"""
        self.current_image_path = None
        self.preview_image = None
        self.show_default_image()
        self.clear_btn.pack_forget()
    
    def show_default_image(self):
        """Mostrar imagen por defecto"""
        default_image = self.image_manager.get_default_image((150, 150))
        self.image_label.configure(image=default_image, text="")
        self.info_label.configure(
            text="🖼️ Arrastra una imagen aquí\nó haz clic para seleccionar",
            text_color="lightblue"
        )
    
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