import customtkinter as ctk
from reporte_ventas_manager import ReporteVentasManager
from reporte_inventario_manager import ReporteInventarioManager
from dashboard_manager import DashboardManager

class MainReportesManager:
    def __init__(self, parent, default_tab="📊 Ventas"):
        self.parent = parent
        self.default_tab = default_tab
        self.main_frame = None
        self.tabview = None
        
        # Referencias a los módulos hijos
        self.reporte_ventas = None
        self.reporte_inventario = None
        self.dashboard = None
        
        self._setup_ui()

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Crear Tabview
        self.tabview = ctk.CTkTabview(self.main_frame)
        self.tabview.pack(fill="both", expand=True)
        
        # Agregar pestañas
        self.tabview.add("📊 Ventas")
        self.tabview.add("📦 Valorización de Inventario")
        self.tabview.add("📈 Dashboard")
        
        # Instanciar el Reporte de Ventas en la primera pestaña
        tab_ventas = self.tabview.tab("📊 Ventas")
        self.reporte_ventas = ReporteVentasManager(tab_ventas)
        
        # Instanciar la Valorización de Inventario en la segunda pestaña
        tab_inventario = self.tabview.tab("📦 Valorización de Inventario")
        self.reporte_inventario = ReporteInventarioManager(tab_inventario)
        
        # Instanciar el Dashboard en la tercera pestaña
        tab_dashboard = self.tabview.tab("📈 Dashboard")
        self.dashboard = DashboardManager(tab_dashboard)

        # Seleccionar la pestaña por defecto
        try:
            self.tabview.set(self.default_tab)
        except ValueError:
            pass
