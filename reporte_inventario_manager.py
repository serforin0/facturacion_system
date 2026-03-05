import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
from database import Database
import os
from datetime import datetime
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

class ReporteInventarioManager:
    def __init__(self, parent):
        self.parent = parent
        self.db = Database()

        self.main_frame = None
        self.tree_inventario = None
        self.lbl_total_costo = None
        self.lbl_total_venta = None
        
        self._setup_ui()
        self.load_data()

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # ====== HEADER ======
        header_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        header_frame.pack(side="top", fill="x", padx=5, pady=5)
        
        lbl_title = ctk.CTkLabel(
            header_frame,
            text="Valorización de Inventario",
            font=("Arial", 16, "bold"),
            text_color="white"
        )
        lbl_title.pack(side="left", padx=10, pady=10)
        
        btn_refresh = ctk.CTkButton(
            header_frame,
            text="🔄 Actualizar",
            width=100,
            command=self.load_data
        )
        btn_refresh.pack(side="right", padx=10, pady=10)

        btn_export = ctk.CTkButton(
            header_frame,
            text="📥 Exportar PDF",
            width=100,
            fg_color="#A33",
            command=self.export_to_pdf
        )
        btn_export.pack(side="right", padx=10, pady=10)

        # ====== FOOTER (TOTALES) ======
        footer_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        footer_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        
        self.lbl_total_costo = ctk.CTkLabel(
            footer_frame,
            text="Valor Total Inventario (Costo): RD$ 0.00",
            font=("Arial", 14, "bold"),
            text_color="#e67e22"
        )
        self.lbl_total_costo.pack(side="left", padx=20, pady=15)
        
        self.lbl_total_venta = ctk.CTkLabel(
            footer_frame,
            text="Valor Total Inventario (Venta): RD$ 0.00",
            font=("Arial", 14, "bold"),
            text_color="#2ecc71"
        )
        self.lbl_total_venta.pack(side="right", padx=20, pady=15)

        # ====== DATAGRID ======
        table_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        table_frame.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        columns = (
            "producto", 
            "categoria", 
            "stock", 
            "precio_costo", 
            "precio_venta", 
            "valor_costo", 
            "valor_venta"
        )

        self.tree_inventario = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=15
        )

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=25,
            fieldbackground="#2a2d2e",
            borderwidth=0
        )
        style.configure(
            "Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 11, "bold")
        )
        style.map('Treeview', background=[('selected', '#22559b')])

        headers = [
            ("Producto", 160),
            ("Categoría", 110),
            ("Stock", 60),
            ("Costo Unit.", 110),
            ("Precio Venta", 110),
            ("Valor Total (Costo)", 130),
            ("Valor Total (Venta)", 130),
        ]

        for (col, (text, width)) in zip(columns, headers):
            self.tree_inventario.heading(col, text=text)
            # Alinear números a la derecha, excepto nombre y categoría
            anchor = "center" if col in ("producto", "categoria") else "e"
            self.tree_inventario.column(col, width=width, anchor=anchor)

        scroll_y = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree_inventario.yview
        )
        scroll_x = ttk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=self.tree_inventario.xview
        )
        self.tree_inventario.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_x.pack(side="bottom", fill="x")
        scroll_y.pack(side="right", fill="y")
        self.tree_inventario.pack(side="left", fill="both", expand=True)

    def load_data(self):
        # Limpiar
        for item in self.tree_inventario.get_children():
            self.tree_inventario.delete(item)
            
        try:
            filas, total_costo, total_venta = self.db.get_inventory_valuation()
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al cargar la valorización:\n{e}")
            return
            
        for row in filas:
            nombre, categoria, stock, p_base, p_venta, v_costo, v_venta = row
            
            p_base = float(p_base or 0)
            p_venta = float(p_venta or 0)
            v_costo = float(v_costo or 0)
            v_venta = float(v_venta or 0)
            
            self.tree_inventario.insert(
                "",
                "end",
                values=(
                    nombre,
                    categoria or "Sin categoría",
                    f"{stock}",
                    f"RD$ {p_base:,.2f}",
                    f"RD$ {p_venta:,.2f}",
                    f"RD$ {v_costo:,.2f}",
                    f"RD$ {v_venta:,.2f}"
                )
            )
            
        # Actualizar labels totalizadores
        self.lbl_total_costo.configure(text=f"Valor Total Inventario (Costo): RD$ {total_costo:,.2f}")
        self.lbl_total_venta.configure(text=f"Valor Total Inventario (Venta): RD$ {total_venta:,.2f}")

    def export_to_pdf(self):
        # Ask for save destination
        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            title="Guardar Reporte de Inventario como PDF",
            initialfile=f"Reporte_Inventario_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        )

        if not file_path:
            return  # Cancelado por el usuario

        try:
            doc = SimpleDocTemplate(file_path, pagesize=landscape(letter))
            elements = []
            
            styles = getSampleStyleSheet()
            title_style = styles['Title']
            subtitle_style = styles['Normal']
            
            # Title
            elements.append(Paragraph("Reporte de Valorización de Inventario", title_style))
            elements.append(Paragraph(f"Fecha de generación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", subtitle_style))
            elements.append(Spacer(1, 20))
            
            # Table Data
            data = [["Producto", "Categoría", "Stock", "Costo", "Venta", "V. Costo", "V. Venta"]]
            
            # Extract from treeview
            for child in self.tree_inventario.get_children():
                row = self.tree_inventario.item(child)["values"]
                data.append([str(x) for x in row])
                
            # Totals at the end
            t_costo = self.lbl_total_costo.cget("text").split(":")[-1].strip()
            t_venta = self.lbl_total_venta.cget("text").split(":")[-1].strip()
            data.append(["", "", "", "", "TOTALES:", t_costo, t_venta])
            
            table = Table(data, repeatRows=1)
            
            # Table Style
            style = TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2B2B2B")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -2), colors.whitesmoke),
                ('TEXTCOLOR', (0, 1), (-1, -2), colors.black),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -2), 1, colors.black),
                # Totals row style
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#DDDDDD")),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.black),
                ('GRID', (4, -1), (-1, -1), 1, colors.black),
            ])
            
            table.setStyle(style)
            elements.append(table)
            
            doc.build(elements)
            messagebox.showinfo("Exportación Exitosa", f"Reporte PDF guardado correctamente en:\n{file_path}")
            
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al exportar a PDF:\n{e}")
