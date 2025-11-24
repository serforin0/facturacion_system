import customtkinter as ctk
import tkinterdnd2 as tkdnd
from inventory_manager import InventoryManager
from styles import Styles

# Crear una clase que herede de tkinterdnd2 y use CustomTkinter
class DnDCTk(tkdnd.TkinterDnD.Tk if hasattr(tkdnd, 'TkinterDnD') else ctk.CTk):
    def __init__(self):
        if hasattr(tkdnd, 'TkinterDnD'):
            # Usar tkinterdnd2 si está disponible
            super().__init__()
            # Configurar CustomTkinter sobre Tkinter normal
            self._apply_ctk_theme()
        else:
            # Fallback a CustomTkinter normal
            super().__init__()
    
    def _apply_ctk_theme(self):
        """Aplicar tema de CustomTkinter a ventana Tkinter normal"""
        self.configure(bg='#2B2B2B')  # Fondo oscuro
        self.option_add('*background', '#2B2B2B')
        self.option_add('*foreground', 'white')
        self.option_add('*Button.background', '#2B5F87')
        self.option_add('*Button.foreground', 'white')

class BarSystemApp:
    def __init__(self):
        # Configurar tema PRIMERO
        Styles.setup_theme()
        
        # Crear ventana principal con soporte DnD
        self.root = DnDCTk()
        self.root.title("🍻 Sistema de Bar - Inventario")
        self.root.geometry("1200x700")
        self.root.minsize(1000, 600)
        
        # Configurar fondo oscuro explícitamente
        if hasattr(tkdnd, 'TkinterDnD'):
            self.root.configure(bg='#2B2B2B')
        else:
            self.root.configure(fg_color='#2B2B2B')
        
        self.setup_ui()
    
    def setup_ui(self):
        # Frame de fondo principal
        main_bg = ctk.CTkFrame(self.root, fg_color='#2B2B2B')
        main_bg.pack(fill="both", expand=True)
        
        # Barra de título
        title_label = ctk.CTkLabel(
            main_bg, 
            text="🍻 SISTEMA DE GESTIÓN PARA BAR", 
            font=("Arial", 24, "bold"),
            height=60,
            fg_color="#2B5F87",
            text_color="white"
        )
        title_label.pack(fill="x", padx=10, pady=10)
        
        # Frame principal para módulos
        self.main_container = ctk.CTkFrame(main_bg, fg_color='#2B2B2B')
        self.main_container.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Inicializar módulo de inventario
        self.inventory_manager = InventoryManager(self.main_container)
    
    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = BarSystemApp()
    app.run()