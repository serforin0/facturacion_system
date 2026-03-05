import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from database import Database

class DashboardManager:
    def __init__(self, parent):
        self.parent = parent
        self.db = Database()
        
        self.main_frame = None
        self.fig_bar = None
        self.fig_pie = None
        self.canvas_bar = None
        self.canvas_pie = None
        
        self._setup_ui()
        self.load_data()

    def _setup_ui(self):
        self.main_frame = ctk.CTkScrollableFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Header
        header_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        header_frame.pack(fill="x", padx=5, pady=5)
        
        lbl_title = ctk.CTkLabel(
            header_frame,
            text="📈 Dashboard de Indicadores",
            font=("Arial", 18, "bold"),
            text_color="white"
        )
        lbl_title.pack(side="left", padx=10, pady=10)
        
        btn_refresh = ctk.CTkButton(
            header_frame,
            text="🔄 Actualizar Gráficos",
            width=120,
            command=self.load_data
        )
        btn_refresh.pack(side="right", padx=10, pady=10)

        # Charts Container
        self.charts_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        self.charts_frame.pack(fill="both", expand=True, padx=5, pady=10)
        
        # Configure grid for two charts side by side (if space permits)
        self.charts_frame.columnconfigure(0, weight=1)
        self.charts_frame.columnconfigure(1, weight=1)
        
        self.frame_bar = ctk.CTkFrame(self.charts_frame, fg_color="#1F1F1F")
        self.frame_bar.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        
        self.frame_pie = ctk.CTkFrame(self.charts_frame, fg_color="#1F1F1F")
        self.frame_pie.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        # Titles for each chart
        ctk.CTkLabel(self.frame_bar, text="Ventas de los Últimos 7 Días", font=("Arial", 14, "bold")).pack(pady=5)
        ctk.CTkLabel(self.frame_pie, text="Valorización del Inventario (Costo)", font=("Arial", 14, "bold")).pack(pady=5)

    def load_data(self):
        # 1. Bar Chart Data (Sales last 7 days)
        sales_data = self.db.get_sales_last_7_days()
        dates = [row[0] for row in sales_data]
        totals = [row[1] for row in sales_data]
        
        # 2. Pie Chart Data (Inventory valuation by category)
        inv_data = self.db.get_inventory_valuation_by_category()
        categories = [row[0] for row in inv_data]
        values = [row[1] for row in inv_data]
        
        self.render_bar_chart(dates, totals)
        self.render_pie_chart(categories, values)

    def render_bar_chart(self, x_data, y_data):
        if self.canvas_bar:
            self.canvas_bar.get_tk_widget().destroy()
            
        fig = Figure(figsize=(5, 4), dpi=100)
        fig.patch.set_facecolor('#1F1F1F')
        
        ax = fig.add_subplot(111)
        ax.set_facecolor('#1F1F1F')
        ax.tick_params(axis='x', colors='white')
        ax.tick_params(axis='y', colors='white')
        
        if not x_data:
            ax.text(0.5, 0.5, "No hay datos de ventas recientes", 
                    color="white", ha="center", va="center", transform=ax.transAxes)
        else:
            ax.bar(x_data, y_data, color='#3498db')
            
            # Rotate x labels for better visibility
            fig.autofmt_xdate()
        
        self.canvas_bar = FigureCanvasTkAgg(fig, master=self.frame_bar)
        self.canvas_bar.draw()
        self.canvas_bar.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

    def render_pie_chart(self, labels, sizes):
        if self.canvas_pie:
            self.canvas_pie.get_tk_widget().destroy()
            
        fig = Figure(figsize=(5, 4), dpi=100)
        fig.patch.set_facecolor('#1F1F1F')
        
        ax = fig.add_subplot(111)
        
        if not labels or sum(sizes) == 0:
            ax.set_facecolor('#1F1F1F')
            ax.text(0.5, 0.5, "No hay inventario activo", 
                    color="white", ha="center", va="center", transform=ax.transAxes)
            ax.axis('off')
        else:
            # Use a colorful colormap
            cmap = plt.get_cmap('tab20')
            colors = cmap(range(len(labels)))
            
            wedges, texts, autotexts = ax.pie(
                sizes, 
                labels=labels, 
                autopct='%1.1f%%', 
                startangle=140, 
                colors=colors,
                textprops=dict(color="w")
            )
            ax.axis('equal')  # Equal aspect ratio ensures that pie is drawn as a circle.
            
        self.canvas_pie = FigureCanvasTkAgg(fig, master=self.frame_pie)
        self.canvas_pie.draw()
        self.canvas_pie.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
