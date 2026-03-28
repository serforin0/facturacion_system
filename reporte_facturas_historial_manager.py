"""
Historial de facturas en Reportería (misma idea que valorización de inventario):
filtros, tabla, totales, PDF y exportación CSV.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from config_impresion_dialog import open_config_impresion
from database import Database
from report_pdf_builder import build_facturas_historial_pdf
from report_preview_window import ReportPreviewWindow


def _fmt_fecha_corta(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 19:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s[:16]


class ReporteFacturasHistorialManager:
    def __init__(self, parent):
        self.parent = parent
        self.db = Database()
        self._ultimo_pdf: bytes | None = None
        self._ultimo_subtitulo_pdf: str = ""
        self._rows_cache: list[tuple] = []

        self.main_frame = None
        self.tree = None
        self.lbl_resumen = None

        self.entry_fecha_desde = None
        self.entry_fecha_hasta = None
        self.entry_usuario = None
        self.entry_texto = None
        self.combo_estado = None

        self._setup_ui()
        self.load_data()

    def _setup_ui(self):
        self.main_frame = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        self.main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        header_frame = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        header_frame.pack(side="top", fill="x", padx=5, pady=5)

        ctk.CTkLabel(
            header_frame,
            text="Historial de facturas",
            font=("Arial", 16, "bold"),
            text_color="white",
        ).pack(side="left", padx=10, pady=10)

        ctk.CTkButton(
            header_frame,
            text="🔄 Actualizar",
            width=100,
            command=self.load_data,
        ).pack(side="right", padx=6, pady=10)

        ctk.CTkButton(
            header_frame,
            text="📥 Exportar CSV",
            width=120,
            fg_color="#7c3aed",
            command=self._exportar_csv,
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
            text="⚙ Empresa / impresión",
            width=150,
            fg_color="#475569",
            command=lambda: open_config_impresion(
                self.main_frame.winfo_toplevel(), self.db
            ),
        ).pack(side="right", padx=6, pady=10)

        filt = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        filt.pack(fill="x", padx=5, pady=(0, 5))

        hoy = datetime.now().date()
        ctk.CTkLabel(filt, text="Desde (AAAA-MM-DD)", font=("Arial", 11)).grid(
            row=0, column=0, padx=6, pady=6, sticky="w"
        )
        self.entry_fecha_desde = ctk.CTkEntry(filt, width=120)
        self.entry_fecha_desde.grid(row=0, column=1, padx=4, pady=6, sticky="w")
        self.entry_fecha_desde.insert(0, (hoy - timedelta(days=90)).isoformat())

        ctk.CTkLabel(filt, text="Hasta", font=("Arial", 11)).grid(
            row=0, column=2, padx=6, pady=6, sticky="w"
        )
        self.entry_fecha_hasta = ctk.CTkEntry(filt, width=120)
        self.entry_fecha_hasta.grid(row=0, column=3, padx=4, pady=6, sticky="w")
        self.entry_fecha_hasta.insert(0, hoy.isoformat())

        ctk.CTkLabel(filt, text="Usuario", font=("Arial", 11)).grid(
            row=0, column=4, padx=6, pady=6, sticky="w"
        )
        self.entry_usuario = ctk.CTkEntry(filt, width=100, placeholder_text="opcional")
        self.entry_usuario.grid(row=0, column=5, padx=4, pady=6, sticky="w")

        ctk.CTkLabel(filt, text="Estado", font=("Arial", 11)).grid(
            row=1, column=0, padx=6, pady=6, sticky="w"
        )
        self.combo_estado = ctk.CTkComboBox(
            filt,
            values=["Todos", "Solo emitidas", "Solo anuladas"],
            width=140,
        )
        self.combo_estado.set("Todos")
        self.combo_estado.grid(row=1, column=1, padx=4, pady=6, sticky="w")

        ctk.CTkLabel(
            filt,
            text="Nº / cliente / RNC",
            font=("Arial", 11),
        ).grid(row=1, column=2, columnspan=2, padx=6, pady=6, sticky="w")
        self.entry_texto = ctk.CTkEntry(filt, width=220, placeholder_text="opcional")
        self.entry_texto.grid(row=1, column=4, columnspan=2, padx=4, pady=6, sticky="w")

        ctk.CTkButton(filt, text="Aplicar filtros", width=110, command=self.load_data).grid(
            row=1, column=6, padx=8, pady=6
        )
        ctk.CTkButton(
            filt,
            text="Limpiar",
            width=90,
            fg_color="#64748b",
            command=self._limpiar_filtros,
        ).grid(row=1, column=7, padx=4, pady=6)

        footer = ctk.CTkFrame(self.main_frame, fg_color="#1F1F1F")
        footer.pack(side="bottom", fill="x", padx=5, pady=5)

        self.lbl_resumen = ctk.CTkLabel(
            footer,
            text="",
            font=("Arial", 12, "bold"),
            text_color="#94a3b8",
        )
        self.lbl_resumen.pack(side="left", padx=16, pady=10)

        table_frame = ctk.CTkFrame(self.main_frame, fg_color="#2B2B2B")
        table_frame.pack(fill="both", expand=True, padx=5, pady=5)

        columns = (
            "numero",
            "fecha",
            "cliente",
            "doc_c",
            "usuario",
            "tipo",
            "pago",
            "subtotal",
            "desc",
            "itbis",
            "total",
            "estado",
        )

        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=18
        )

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "RepFact.Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=22,
            fieldbackground="#2a2d2e",
            borderwidth=0,
        )
        style.configure(
            "RepFact.Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            relief="flat",
            font=("Arial", 10, "bold"),
        )
        style.map("RepFact.Treeview", background=[("selected", "#22559b")])

        self.tree.configure(style="RepFact.Treeview")

        headers = [
            ("Número", 100, "center"),
            ("Fecha", 130, "center"),
            ("Cliente", 200, "w"),
            ("Doc.", 100, "center"),
            ("Usuario", 90, "center"),
            ("Tipo", 100, "center"),
            ("Pago", 100, "center"),
            ("Subtotal", 82, "e"),
            ("Desc.", 72, "e"),
            ("ITBIS", 72, "e"),
            ("Total", 82, "e"),
            ("Estado", 80, "center"),
        ]
        for col, (text, width, anchor) in zip(columns, headers):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor)

        sy = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        sx = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.tree.xview
        )
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

    def _estado_param(self) -> str:
        v = (self.combo_estado.get() or "").lower()
        if "anulad" in v:
            return "anulada"
        if "emitid" in v:
            return "emitida"
        return "todos"

    def _subtitulo_filtros(self) -> str:
        parts = []
        d1 = (self.entry_fecha_desde.get() or "").strip()
        d2 = (self.entry_fecha_hasta.get() or "").strip()
        if d1 or d2:
            parts.append(f"Período: {d1 or '…'} — {d2 or '…'}")
        u = (self.entry_usuario.get() or "").strip()
        if u:
            parts.append(f"Usuario: {u}")
        parts.append(f"Estado: {self.combo_estado.get() or 'Todos'}")
        t = (self.entry_texto.get() or "").strip()
        if t:
            parts.append(f"Búsqueda: {t}")
        return " · ".join(parts) if parts else "Sin filtros restrictivos (límite de registros aplicado)."

    def _limpiar_filtros(self):
        hoy = datetime.now().date()
        self.entry_fecha_desde.delete(0, "end")
        self.entry_fecha_desde.insert(0, (hoy - timedelta(days=90)).isoformat())
        self.entry_fecha_hasta.delete(0, "end")
        self.entry_fecha_hasta.insert(0, hoy.isoformat())
        self.entry_usuario.delete(0, "end")
        self.entry_texto.delete(0, "end")
        self.combo_estado.set("Todos")
        self.load_data()

    def load_data(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        fd = (self.entry_fecha_desde.get() or "").strip() or None
        fh = (self.entry_fecha_hasta.get() or "").strip() or None
        us = (self.entry_usuario.get() or "").strip() or None
        tx = (self.entry_texto.get() or "").strip() or None

        try:
            rows = self.db.fetch_facturas_historial_reporte(
                fecha_desde=fd,
                fecha_hasta=fh,
                usuario=us,
                estado=self._estado_param(),
                texto=tx,
                limit=5000,
            )
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar el historial:\n{e}")
            return

        self._rows_cache = rows
        subt = self._subtitulo_filtros()
        self._ultimo_subtitulo_pdf = subt

        sum_emitidas = 0.0
        n_emitidas = 0
        for row in rows:
            (
                _id,
                numero,
                fecha,
                cliente,
                doc_c,
                usuario,
                tipo,
                pago,
                sub,
                desc,
                itb,
                total,
                estado,
            ) = row
            if (estado or "").lower() == "emitida":
                sum_emitidas += float(total or 0)
                n_emitidas += 1
            self.tree.insert(
                "",
                "end",
                values=(
                    numero or "",
                    _fmt_fecha_corta(fecha),
                    (cliente or "")[:60],
                    doc_c or "",
                    usuario or "",
                    tipo or "",
                    pago or "",
                    f"{float(sub or 0):,.2f}",
                    f"{float(desc or 0):,.2f}",
                    f"{float(itb or 0):,.2f}",
                    f"{float(total or 0):,.2f}",
                    estado or "",
                ),
            )

        self.lbl_resumen.configure(
            text=(
                f"Registros mostrados: {len(rows):,}  |  "
                f"Facturas emitidas en lista: {n_emitidas:,}  |  "
                f"Suma total (emitidas en lista): RD$ {sum_emitidas:,.2f}"
            )
        )

        try:
            self._ultimo_pdf = build_facturas_historial_pdf(
                self.db, rows, subtitulo_filtros=subt
            )
        except Exception as e:
            self._ultimo_pdf = None
            print("PDF historial facturas:", e)

    def _pdf_actual(self) -> bytes:
        if self._ultimo_pdf:
            return self._ultimo_pdf
        return build_facturas_historial_pdf(
            self.db,
            self._rows_cache,
            subtitulo_filtros=self._ultimo_subtitulo_pdf,
        )

    def _vista_previa(self):
        pdf = self._pdf_actual()
        ReportPreviewWindow(
            self.main_frame.winfo_toplevel(),
            pdf,
            title="Vista previa — Historial de facturas",
        )

    def _guardar_pdf(self):
        pdf = self._pdf_actual()
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            title="Guardar historial de facturas",
            initialfile=f"Historial_facturas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
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
        import os
        import tempfile

        pdf = self._pdf_actual()
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(pdf)
            if _print_pdf_path(path):
                messagebox.showinfo(
                    "Imprimir", "Enviado a la impresora predeterminada."
                )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _exportar_csv(self):
        if not self._rows_cache:
            messagebox.showinfo("CSV", "No hay datos para exportar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            title="Exportar historial de facturas",
            initialfile=f"Historial_facturas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        headers = [
            "id",
            "numero",
            "fecha",
            "cliente",
            "documento_cliente",
            "usuario",
            "tipo_comprobante",
            "formas_pago",
            "subtotal",
            "descuento_total",
            "impuesto_total",
            "total",
            "estado",
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(headers)
                for row in self._rows_cache:
                    w.writerow(row)
            messagebox.showinfo("CSV", f"Exportado:\n{path}")
        except OSError as e:
            messagebox.showerror("Error", str(e))
