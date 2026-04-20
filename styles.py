# Configuración de estilos para customtkinter
class Styles:
    # Colores principales
    PRIMARY = "#2B5F87"
    SECONDARY = "#4CAF50"
    DANGER = "#F44336"
    WARNING = "#FF9800"

    @staticmethod
    def setup_theme_from_db(db=None):
        """Aplica modo (claro/oscuro/sistema) y tema de color desde config, o valores por defecto."""
        import customtkinter as ctk

        mode = "dark"
        theme = "blue"
        if db is not None:
            m = (db.get_config("ui_appearance_mode", "") or "").strip().lower()
            t = (db.get_config("ui_color_theme", "") or "").strip().lower()
            if m in ("dark", "light", "system"):
                mode = m
            if t in ("blue", "green", "dark-blue"):
                theme = t
        ctk.set_appearance_mode(mode)
        ctk.set_default_color_theme(theme)

    @staticmethod
    def apply_root_window_bg(root, db=None):
        """Ajusta el fondo de la ventana principal según modo claro u oscuro."""
        mode = "dark"
        if db is not None:
            m = (db.get_config("ui_appearance_mode", "") or "").strip().lower()
            if m in ("dark", "light", "system"):
                mode = m
        try:
            if mode == "light":
                root.configure(fg_color="#E8E8E8")
            else:
                root.configure(fg_color="#2B2B2B")
        except Exception:
            pass

    # Configuración de temas (sin BD; compatibilidad)
    @staticmethod
    def setup_theme():
        Styles.setup_theme_from_db(None)
    
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