# Configuración de estilos para customtkinter
class Styles:
    # Colores principales
    PRIMARY = "#2B5F87"
    SECONDARY = "#4CAF50"
    DANGER = "#F44336"
    WARNING = "#FF9800"
    
    # Configuración de temas
    @staticmethod
    def setup_theme():
        import customtkinter as ctk
        ctk.set_appearance_mode("dark")  # "light" o "dark"
        ctk.set_default_color_theme("blue")  # Temas: blue, green, dark-blue
    
    # Estilos para botones
    @staticmethod
    def get_button_style():
        return {
            "corner_radius": 8,
            "height": 40,
            "font": ("Arial", 14, "bold")
        }
    
    # Estilos para frames
    @staticmethod
    def get_frame_style():
        return {
            "corner_radius": 10,
            "border_width": 2,
            "border_color": "#3B3B3B"
        }