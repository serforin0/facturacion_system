import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw
import os
import sys
import subprocess

class ModernImageSelector(ctk.CTkFrame):
    def __init__(self, master, image_manager, *, light_theme=False, **kwargs):
        self._light = light_theme
        super().__init__(master, **kwargs)
        self.image_manager = image_manager
        self.current_image_path = None
        self.preview_image = None
        self.setup_ui()
    
    def setup_ui(self):
        if self._light:
            self.configure(fg_color=("#EDE8DE", "#3A3835"), corner_radius=10)
            img_bg = ("#E8E0D4", "#2F2E2C")
            img_border = ("#C4B8A8", "#555555")
        else:
            self.configure(fg_color="#2a2d2e", corner_radius=10)
            img_bg = "#3B3B3B"
            img_border = "#4A4A4A"

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 0))
        self.buscar_top_btn = ctk.CTkButton(
            top,
            text="Buscar",
            width=72,
            height=28,
            command=self.select_image,
            fg_color=("#2563EB", "#1D4ED8") if self._light else ("#2B5F87", "#1E4260"),
        )
        self.buscar_top_btn.pack(side="left", padx=(0, 6))
        self.foto_top_btn = ctk.CTkButton(
            top,
            text="Foto",
            width=72,
            height=28,
            command=self._tomar_foto,
            fg_color=("#64748B", "#475569") if self._light else ("#4A5568", "#2D3748"),
        )
        self.foto_top_btn.pack(side="left", padx=0)

        # Frame para la zona de imagen
        self.image_frame = ctk.CTkFrame(
            self, 
            fg_color=img_bg, 
            corner_radius=10,
            border_width=2,
            border_color=img_border,
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
            text="🖼️ Clic en la imagen\no use Buscar / Foto",
            text_color=("#1e40af", "#93C5FD") if self._light else "lightblue",
            font=("Arial", 11, "bold"),
            wraplength=150
        )
        self.info_label.pack(pady=(0, 10))
        
        # Botón extra (compat. solo lectura / accesibilidad)
        self.select_btn = ctk.CTkButton(
            self.image_frame,
            text="Elegir archivo…",
            command=self.select_image,
            fg_color=("#2B5F87", "#1E4260"),
            width=130,
            height=30,
            font=("Arial", 10, "bold")
        )
        self.select_btn.pack(pady=(0, 8))
        
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

    def _tomar_foto(self):
        """Abre la app de cámara del sistema si existe; si no, instrucciones."""
        if sys.platform == "darwin":
            try:
                subprocess.Popen(
                    ["open", "-a", "Photo Booth"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                messagebox.showinfo(
                    "Foto",
                    "Se intentó abrir «Photo Booth». Guarde la foto y luego use «Buscar» "
                    "para asignarla al producto.",
                )
                return
            except Exception:
                pass
        messagebox.showinfo(
            "Foto",
            "Use «Buscar» para elegir una imagen desde archivos.\n\n"
            "Si tomó una foto con el teléfono, transfiérala a esta PC y selecciónela con Buscar.",
        )
    
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
                text="✅ Imagen cargada",
                text_color=("#15803d", "#86EFAC") if self._light else "#4CAF50"
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
            text="🖼️ Clic en la imagen\no use Buscar / Foto",
            text_color=("#1e40af", "#93C5FD") if self._light else "lightblue",
        )
    
    def load_existing_image(self, image_path):
        """Cargar una imagen existente en el selector"""
        if image_path and os.path.exists(image_path):
            try:
                self.load_image_from_path(image_path)
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