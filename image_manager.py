import os
import shutil
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw
import customtkinter as ctk

from app_paths import data_directory, is_frozen


class ImageManager:
    def __init__(self):
        self.images_dir = (
            os.path.join(data_directory(), "product_images")
            if is_frozen()
            else "product_images"
        )
        self.create_images_directory()
    
    def create_images_directory(self):
        """Crear directorio para imágenes si no existe"""
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
    
    def copy_image_to_app(self, source_path, product_id):
        """Copiar imagen al directorio de la aplicación"""
        if not source_path:
            return None
        
        try:
            # Obtener extensión del archivo
            file_ext = os.path.splitext(source_path)[1].lower()
            
            # Validar que sea una imagen válida para abrir
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
            if file_ext not in valid_extensions:
                messagebox.showerror("Error", "Formato de imagen no válido. Use JPG, PNG, WEBP, GIF o BMP.")
                return None
            
            # Nombre del archivo destino como WEBP
            dest_filename = f"product_{product_id}.webp"
            dest_path = os.path.join(self.images_dir, dest_filename)
            
            # Redimensionar a 250x250 y guardar como WEBP
            with Image.open(source_path) as img:
                # Convertir a RGB si tiene paleta para evitar errores de guardado
                if img.mode in ("RGBA", "P"): 
                    img = img.convert("RGBA")
                
                img_resized = img.resize((250, 250), Image.Resampling.LANCZOS)
                img_resized.save(dest_path, "WEBP", quality=85)
            
            return dest_path
            
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo copiar la imagen: {str(e)}")
            return None
    
    def get_image_path(self, product_id):
        """Obtener ruta de imagen para un producto"""
        if not product_id:
            return None
        
        # Buscar cualquier imagen que coincida con el product_id
        for ext in ['.webp', '.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            possible_path = os.path.join(self.images_dir, f"product_{product_id}{ext}")
            if os.path.exists(possible_path):
                return possible_path
        
        return None
    
    def load_image_for_display(self, image_path, size=(100, 100)):
        """Cargar imagen para mostrar en la interfaz"""
        if not image_path or not os.path.exists(image_path):
            # Retornar imagen por defecto
            return self.get_default_image(size)
        
        try:
            image = Image.open(image_path)
            image = image.resize(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception as e:
            print(f"Error cargando imagen: {str(e)}")
            return self.get_default_image(size)
    
    def get_default_image(self, size=(100, 100)):
        """Crear imagen por defecto"""
        image = Image.new('RGB', size, color='#2B5F87')
        draw = ImageDraw.Draw(image)
        
        # Dibujar ícono de cámara
        draw.rectangle([20, 20, size[0]-20, size[1]-20], outline='white', width=2)
        draw.ellipse([size[0]//2-15, size[1]//2-15, size[0]//2+15, size[1]//2+15], 
                    outline='white', width=2)
        
        return ImageTk.PhotoImage(image)
    
    def delete_product_image(self, product_id):
        """Eliminar imagen de un producto"""
        image_path = self.get_image_path(product_id)
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
                return True
            except:
                return False
        return True