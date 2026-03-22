import customtkinter as ctk
from tkinter import messagebox, ttk

from config_impresion_dialog import open_config_impresion
from database import Database
from report_pdf_builder import build_inventory_valuation_pdf
from report_preview_window import ReportPreviewWindow


class ReporteInventarioManager:
    """Reporte «Valor del inventario» tipo MONICA + vista previa PDF sin guardar obligatorio."""

    def __init__(self, parent):
        self.parent = parent
        self.db = Database()
        self.var_incluir_cero = ctk.BooleanVar(value=False)
        self._ultimo_pdf: bytes | None = None

        self.main_frame = None
        self.tree_inventario = None
        self.lbl_total_costo = None
        self.lbl_total_venta = None
        self.lbl_items = None

        self._setup_ui()
        self.load_data()

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        header_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        header_frame.pack(side="top", fill="x", padx=5, pady=5)

        ctk.CTkLabel(
            header_frame,
            text="Valor del inventario",
            font=("Arial", 16, "bold"),
            text_color="white",
        ).pack(side="left", padx=10, pady=10)

        ctk.CTkCheckBox(
            header_frame,
            text="Incluir productos con stock 0",
            variable=self.var_incluir_cero,
            command=self.load_data,
        ).pack(side="left", padx=12)

        ctk.CTkButton(
            header_frame,
            text="⚙ Empresa / impresión",
            width=150,
            fg_color="#475569",
            command=lambda: open_config_impresion(self.main_frame.winfo_toplevel(), self.db),
        ).pack(side="right", padx=6, pady=10)

        ctk.CTkButton(
            header_frame,
            text="🖨 Imprimir PDF",
            width=120,
            fg_color="#047857",
            command=self._imprimir_pdf,
        ).pack(side="right", padx=6, pady=10)

        ctk.CTkButton(
            header_frame,
            text="📥 Guardar PDF",
            width=120,
            fg_color="#B45309",
            command=self._guardar_pdf,
        ).pack(side="right", padx=6, pady=10)

        ctk.CTkButton(
            header_frame,
            text="👁 Vista previa",
            width=120,
            fg_color="#1D4ED8",
            command=self._vista_previa,
        ).pack(side="right", padx=6, pady=10)

        ctk.CTkButton(
            header_frame,
            text="🔄 Actualizar",
            width=100,
            command=self.load_data,
        ).pack(side="right", padx=6, pady=10)

        footer_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        footer_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        self.lbl_items = ctk.CTkLabel(
            footer_frame,
            text="Ítems: 0",
            font=("Arial", 12, "bold"),
            text_color="#94a3b8",
        )
        self.lbl_items.pack(side="left", padx=20, pady=12)

        self.lbl_total_costo = ctk.CTkLabel(
            footer_frame,
            text="Total valor inventario (costo): RD$ 0.00",
            font=("Arial", 13, "bold"),
            text_color="#e67e22",
        )
        self.lbl_total_costo.pack(side="left", padx=20, pady=12)

        self.lbl_total_venta = ctk.CTkLabel(
            footer_frame,
            text="Total valor venta: RD$ 0.00",
            font=("Arial", 13, "bold"),
            text_color="#2ecc71",
        )
        self.lbl_total_venta.pack(side="right", padx=20, pady=12)

        table_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        table_frame.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        columns = (
            "codigo",
            "desc",
            "costo_u",
            "precio_u",
            "stock",
            "v_costo",
            "v_venta",
        )

        self.tree_inventario = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=16
        )

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=24,
            fieldbackground="#2a2d2e",
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#22559b")])

        headers = [
            ("Código", 100, "w"),
            ("Descripción", 220, "w"),
            ("Costo $", 78, "e"),
            ("Precio $", 78, "e"),
            ("En almacén", 72, "e"),
            ("Valor invent.", 88, "e"),
            ("Valor venta", 88, "e"),
        ]
        for col, (text, width, anchor) in zip(columns, headers):
            self.tree_inventario.heading(col, text=text)
            self.tree_inventario.column(col, width=width, anchor=anchor)

        scroll_y = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree_inventario.yview
        )
        scroll_x = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.tree_inventario.xview
        )
        self.tree_inventario.configure(
            yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set
        )

        scroll_x.pack(side="bottom", fill="x")
        scroll_y.pack(side="right", fill="y")
        self.tree_inventario.pack(side="left", fill="both", expand=True)

        sum_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        sum_frame.pack(fill="both", expand=False, padx=5, pady=(8, 5))
        ctk.CTkLabel(
            sum_frame,
            text="📋 Actividad de inventario (kardex) — últimos ~90 días (≈3 meses), agrupado por mes",
            font=("Arial", 13, "bold"),
            text_color="white",
        ).pack(anchor="w", padx=8, pady=(6, 4))

        kcols = ("mes", "tipo_mov", "codigo", "n", "neto", "absol")
        self.tree_kardex_meses = ttk.Treeview(
            sum_frame, columns=kcols, show="headings", height=8
        )
        kh = [
            ("Mes", 90, "center"),
            ("Tipo mov.", 110, "w"),
            ("Cód.", 50, "center"),
            ("Movs", 52, "e"),
            ("Unids. netas", 100, "e"),
            ("Unids. abs.", 90, "e"),
        ]
        for col, (text, width, anchor) in zip(kcols, kh):
            self.tree_kardex_meses.heading(col, text=text)
            self.tree_kardex_meses.column(col, width=width, anchor=anchor)
        sk = ttk.Scrollbar(
            sum_frame, orient="vertical", command=self.tree_kardex_meses.yview
        )
        self.tree_kardex_meses.configure(yscrollcommand=sk.set)
        self.tree_kardex_meses.pack(side="left", fill="both", expand=True, padx=4, pady=(0, 6))
        sk.pack(side="right", fill="y", pady=(0, 6))

    def load_data(self):
        for item in self.tree_inventario.get_children():
            self.tree_inventario.delete(item)
        for item in self.tree_kardex_meses.get_children():
            self.tree_kardex_meses.delete(item)

        try:
            inc = self.var_incluir_cero.get()
            rows, total_costo, total_venta, total_qty, n_items = (
                self.db.get_inventory_valuation_report(include_zero_stock=inc)
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar el reporte:\n{e}")
            return

        for row in rows:
            cod, nom, c_u, p_u, stk, v_c, v_v = row
            self.tree_inventario.insert(
                "",
                "end",
                values=(
                    cod,
                    nom[:80],
                    f"{float(c_u):,.2f}",
                    f"{float(p_u):,.2f}",
                    f"{float(stk):,.2f}",
                    f"{float(v_c):,.2f}",
                    f"{float(v_v):,.2f}",
                ),
            )

        self.lbl_items.configure(text=f"Ítems listados: {n_items}  |  Unidades en almacén: {total_qty:,.2f}")
        self.lbl_total_costo.configure(
            text=f"Total valor inventario (costo): RD$ {total_costo:,.2f}"
        )
        self.lbl_total_venta.configure(
            text=f"Total valor venta: RD$ {total_venta:,.2f}"
        )

        try:
            for row in self.db.get_kardex_resumen_mensual(90):
                ym, tmov, tcod, n_m, s_c, s_a = row
                self.tree_kardex_meses.insert(
                    "",
                    "end",
                    values=(
                        ym or "",
                        tmov or "",
                        (tcod or "")[:12],
                        int(n_m or 0),
                        f"{float(s_c or 0):,.2f}",
                        f"{float(s_a or 0):,.2f}",
                    ),
                )
        except Exception as e:
            print("Resumen kardex mensual:", e)

        try:
            self._ultimo_pdf = build_inventory_valuation_pdf(
                self.db, include_zero_stock=inc
            )
        except Exception as e:
            self._ultimo_pdf = None
            print("PDF buffer:", e)

    def _pdf_actual(self) -> bytes:
        if self._ultimo_pdf:
            return self._ultimo_pdf
        return build_inventory_valuation_pdf(
            self.db, include_zero_stock=self.var_incluir_cero.get()
        )

    def _vista_previa(self):
        pdf = self._pdf_actual()
        ReportPreviewWindow(
            self.main_frame.winfo_toplevel(),
            pdf,
            title="Vista previa — Valor del inventario",
        )

    def _guardar_pdf(self):
        from tkinter import filedialog
        from datetime import datetime

        pdf = self._pdf_actual()
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            title="Guardar valor de inventario",
            initialfile=f"Valor_inventario_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
        )
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(pdf)
            messagebox.showinfo("PDF", f"Guardado en:\n{path}")
        except OSError as e:
            messagebox.showerror("Error", str(e))

    def _imprimir_pdf(self):
        from report_preview_window import _print_pdf_path
        import tempfile
        import os

        pdf = self._pdf_actual()
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(pdf)
            if _print_pdf_path(path):
                messagebox.showinfo("Imprimir", "Enviado a la impresora predeterminada.")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
