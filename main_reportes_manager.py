import customtkinter as ctk

from config_impresion_dialog import open_config_impresion
from database import Database
from reporte_facturas_historial_manager import ReporteFacturasHistorialManager
from reporte_inventario_manager import ReporteInventarioManager
from reporte_ventas_manager import ReporteVentasManager


class MainReportesManager:
    def __init__(self, parent, default_tab="📊 Ventas"):
        self.parent = parent
        self.default_tab = default_tab
        self.main_frame = None
        self.tabview = None
        self.db = Database()

        # Referencias a los módulos hijos
        self.reporte_ventas = None
        self.reporte_facturas_hist = None
        self.reporte_inventario = None

        self._setup_ui()

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        top = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        top.pack(fill="x", padx=5, pady=(4, 0))
        ctk.CTkLabel(
            top,
            text="Reportería",
            font=("Arial", 15, "bold"),
            text_color="white",
        ).pack(side="left", padx=12, pady=8)
        ctk.CTkButton(
            top,
            text="⚙ Impresión y datos de empresa",
            width=200,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: open_config_impresion(
                self.main_frame.winfo_toplevel(), self.db
            ),
        ).pack(side="right", padx=10, pady=8)

        # Crear Tabview
        self.tabview = ctk.CTkTabview(self.main_frame)
        self.tabview.pack(fill="both", expand=True)
        
        # Agregar pestañas
        self.tabview.add("📊 Ventas")
        self.tabview.add("📄 Historial de facturas")
        self.tabview.add("📦 Valorización de Inventario")

        tab_ventas = self.tabview.tab("📊 Ventas")
        self.reporte_ventas = ReporteVentasManager(tab_ventas)

        tab_hist = self.tabview.tab("📄 Historial de facturas")
        self.reporte_facturas_hist = ReporteFacturasHistorialManager(tab_hist)

        tab_inventario = self.tabview.tab("📦 Valorización de Inventario")
        self.reporte_inventario = ReporteInventarioManager(tab_inventario)

        # Seleccionar la pestaña por defecto
        try:
            self.tabview.set(self.default_tab)
        except ValueError:
            pass
