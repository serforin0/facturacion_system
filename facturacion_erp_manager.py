"""
Módulo Facturación (vista tipo ERP — primera fase).
Pestañas, listado de documentos, barra de acciones y diálogos básicos.
El punto de venta (FacturaManager) se abre desde «Crear».
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlencode

import customtkinter as ctk
from tkinter import StringVar, messagebox, ttk

from caja_manager import calcular_totales_desde_apertura, ejecutar_cierre_caja_desde_fila
from database import Database
from devoluciones_facturacion import DevolucionesFacturacionFrame
from factura_manager import FacturaManager
from report_pdf_builder import build_factura_comprobante_pdf


def _fmt_vencimiento(val) -> str:
    if val is None:
        return "—"
    s = str(val).strip()
    if not s:
        return "—"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s[:12]


def _fmt_fecha_factura(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 19:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            pass
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s[:16]


def _fmt_numero_display(numero: str) -> str:
    d = "".join(c for c in str(numero or "") if c.isdigit())
    if d:
        return d.zfill(10)
    return str(numero or "")


def _fmt_monto(v: float) -> str:
    return f"{v:,.2f}"


def _parse_fecha_entrada(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _terminos_pago(total: float, pagado: float) -> str:
    if total - pagado > 0.02:
        return "Pendiente / crédito"
    return "En Efectivo"


def _texto_cierre_caja_turno(
    db: Database,
    caja_row: tuple,
    nombre_operador: str,
    efectivo_contado: float,
    fecha_pantalla: str,
) -> str:
    (
        _cid,
        nombre_caja,
        fecha_ap,
        _fc,
        u_ap,
        _uc,
        monto_inicial,
        _tv,
        _efg,
        _tg,
        _og,
        _ec,
        _df,
        _ob,
        _st,
    ) = caja_row
    monto_inicial = float(monto_inicial or 0)
    total_ventas, ef_sis, tar_sis, otros_sis = calcular_totales_desde_apertura(
        db, fecha_ap
    )
    esperado = ef_sis + monto_inicial
    diff = efectivo_contado - esperado
    lines = [
        "=== CIERRE DE CAJA O TURNO ===",
        f"Caja: {nombre_caja}",
        f"Operador: {nombre_operador or '—'}",
        f"Apertura: {fecha_ap}",
        f"Fecha cierre (registro): {fecha_pantalla}",
        f"Abrió turno: {u_ap or '—'}",
        "",
        f"Monto inicial (fondo): RD$ {monto_inicial:,.2f}",
        f"Ventas (suma pagos, emitidas): RD$ {total_ventas:,.2f}",
        f"  — Efectivo (sistema): RD$ {ef_sis:,.2f}",
        f"  — Tarjeta:           RD$ {tar_sis:,.2f}",
        f"  — Otros:             RD$ {otros_sis:,.2f}",
        "",
        f"Efectivo esperado (fondo + efectivo ventas): RD$ {esperado:,.2f}",
        f"Efectivo contado:                            RD$ {efectivo_contado:,.2f}",
        f"Diferencia (contado − esperado):             RD$ {diff:,.2f}",
        "",
        "(Resumen al momento del cierre; guarde para su archivo.)",
    ]
    return "\n".join(lines)


class FacturacionERPManager:
    FILTRO_TIPOS = [
        "Fecha",
        "Número documento",
        "Cliente",
        "Monto",
        "Producto",
        "Términos",
        "Bodega",
        "Últimos N registros",
        "Todos (sin criterio extra)",
    ]

    def __init__(self, parent, app, current_user=None, current_role=None):
        self.parent = parent
        self.app = app
        self.current_user = current_user
        self.current_role = current_role
        self.db = Database()
        self._pos_shell = None

        self.main = ctk.CTkFrame(parent, fg_color="#2B2B2B")
        self.main.pack(fill="both", expand=True)

        self.combo_estado = None
        self.combo_tipo_filtro = None
        self.frame_filtro_dinamico = None
        self.lbl_total_docs = None
        self.tree = None
        self._fw: dict = {}

        self._build_ui()

    def _toplevel(self):
        w = self.main
        while w is not None and getattr(w, "master", None) is not None:
            w = w.master
        return w

    def _build_ui(self):
        hdr = ctk.CTkFrame(self.main, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(
            hdr,
            text="Facturación",
            font=("Arial", 18, "bold"),
            text_color="white",
        ).pack(side="left")

        self.tabview = ctk.CTkTabview(self.main, height=520)
        self.tabview.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        tab_fact = self.tabview.add("Factura")
        tab_dev = self.tabview.add("Devoluciones")
        tab_lote = self.tabview.add("Facturas en lote")
        tab_rep = self.tabview.add("Reportes")

        self._build_tab_devoluciones(tab_dev)
        self._build_tab_lote(tab_lote)
        self._build_tab_reportes(tab_rep)
        self._build_tab_factura(tab_fact)

    def _build_tab_devoluciones(self, tab_dev: ctk.CTkFrame):
        DevolucionesFacturacionFrame(
            tab_dev, current_user=self.current_user
        ).pack(fill="both", expand=True)

    @staticmethod
    def _parse_lote_presupuestos_text(text: str) -> list[dict]:
        """Agrupa filas: grupo, documento_cliente, codigo_producto, cantidad (coma o punto y coma)."""
        blocks: dict[tuple[str, str], dict] = {}
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.replace(";", ",").split(",")]
            if len(parts) < 4:
                continue
            g, doc, cod, cant_s = parts[0], parts[1], parts[2], parts[3]
            if not cod:
                continue
            try:
                cant = float(cant_s.replace(",", ""))
            except ValueError:
                continue
            if cant <= 0:
                continue
            key = (g, doc)
            if key not in blocks:
                blocks[key] = {
                    "grupo": g,
                    "documento_cliente": doc,
                    "lineas": [],
                }
            blocks[key]["lineas"].append((cod, cant))
        return list(blocks.values())

    def _build_tab_lote(self, tab: ctk.CTkFrame):
        ctk.CTkLabel(
            tab,
            text="Importar presupuestos en lote",
            font=("Arial", 15, "bold"),
            text_color="white",
        ).pack(anchor="w", padx=12, pady=(12, 4))
        ctk.CTkLabel(
            tab,
            text="Una fila por línea: grupo, documento del cliente (RNC/cédula), código de producto, cantidad. "
            "Separe con coma o punto y coma. El cliente debe existir en el maestro. "
            "Se crea un presupuesto por cada grupo distinto.",
            font=("Arial", 11),
            text_color="#94a3b8",
            wraplength=700,
        ).pack(anchor="w", padx=12, pady=(0, 8))
        tb = ctk.CTkTextbox(tab, font=("Consolas", 11), height=220)
        tb.pack(fill="both", expand=True, padx=12, pady=4)
        tb.insert(
            "1.0",
            "# Ejemplo:\n"
            "1,00123456789,SKU01,2\n"
            "1,00123456789,SKU02,1\n"
            "2,00999888777,SKU01,5\n",
        )
        self._txt_lote_presupuestos = tb

        def ejecutar():
            entradas = self._parse_lote_presupuestos_text(tb.get("1.0", "end"))
            if not entradas:
                messagebox.showwarning(
                    "Lote",
                    "No hay filas válidas. Revise el formato.",
                    parent=self._toplevel(),
                )
                return
            n_ok, errores = self.db.importar_lote_presupuestos(
                entradas, self.current_user
            )
            msg = f"Presupuestos creados: {n_ok}."
            if errores:
                msg += "\n\nAvisos:\n" + "\n".join(errores[:25])
                if len(errores) > 25:
                    msg += f"\n… y {len(errores) - 25} más."
            messagebox.showinfo("Importación en lote", msg, parent=self._toplevel())
            self._cargar_lista()

        bf = ctk.CTkFrame(tab, fg_color="transparent")
        bf.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(
            bf,
            text="Importar presupuestos",
            fg_color="#0d9488",
            width=180,
            command=ejecutar,
        ).pack(side="left", padx=4)

    def _build_tab_reportes(self, tab: ctk.CTkFrame):
        ctk.CTkLabel(
            tab,
            text="Reportería",
            font=("Arial", 15, "bold"),
            text_color="white",
        ).pack(anchor="w", padx=12, pady=(16, 8))
        ctk.CTkLabel(
            tab,
            text="Enlace directo al módulo de reportes del sistema (ventas, historial, valorización).",
            font=("Arial", 11),
            text_color="#94a3b8",
            wraplength=640,
        ).pack(anchor="w", padx=12, pady=(0, 16))
        box = ctk.CTkFrame(tab, fg_color="#1e293b")
        box.pack(fill="x", padx=12, pady=8)
        if getattr(self, "app", None) is not None:
            ctk.CTkButton(
                box,
                text="Abrir reportes (ventas e inventario)",
                width=280,
                fg_color="#2563eb",
                command=lambda: self.app.show_reportes(),
            ).pack(padx=16, pady=10, anchor="w")
            ctk.CTkButton(
                box,
                text="Historial de facturas",
                width=280,
                fg_color="#475569",
                command=lambda: self.app.show_historial_facturas(),
            ).pack(padx=16, pady=(0, 14), anchor="w")
        else:
            ctk.CTkLabel(
                box,
                text="Abra Facturación desde el menú principal para usar los atajos.",
                text_color="gray70",
            ).pack(padx=16, pady=16)

    def _build_tab_factura(self, tab_fact: ctk.CTkFrame):
        filt = ctk.CTkFrame(tab_fact, fg_color="#1e293b", corner_radius=6)
        filt.pack(fill="x", padx=4, pady=4)

        ctk.CTkLabel(filt, text="Estado:", font=("Arial", 11)).pack(
            side="left", padx=(8, 4), pady=6
        )
        self.combo_estado = ctk.CTkComboBox(
            filt,
            values=[
                "Todos los documentos",
                "Solo emitidas",
                "Solo presupuestos",
                "Solo anuladas",
            ],
            width=190,
            height=28,
        )
        self.combo_estado.set("Todos los documentos")
        self.combo_estado.pack(side="left", padx=4, pady=6)

        ctk.CTkLabel(filt, text="Documentos por:", font=("Arial", 11)).pack(
            side="left", padx=(16, 4), pady=6
        )
        self.combo_tipo_filtro = ctk.CTkComboBox(
            filt,
            values=self.FILTRO_TIPOS,
            width=220,
            height=28,
            command=lambda _=None: self._refrescar_panel_filtro(),
        )
        self.combo_tipo_filtro.set("Fecha")
        self.combo_tipo_filtro.pack(side="left", padx=4, pady=6)

        self.lbl_total_docs = ctk.CTkLabel(
            filt,
            text="Total de documentos: 0",
            font=("Arial", 11, "bold"),
            text_color="#94a3b8",
        )
        self.lbl_total_docs.pack(side="right", padx=12, pady=6)

        self.frame_filtro_dinamico = ctk.CTkFrame(
            tab_fact, fg_color="#0f172a", corner_radius=6
        )
        self.frame_filtro_dinamico.pack(fill="x", padx=4, pady=(0, 4))
        self._refrescar_panel_filtro()

        tree_fr = ctk.CTkFrame(tab_fact, fg_color="transparent")
        tree_fr.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "FactERP.Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=22,
            fieldbackground="#2a2d2e",
        )
        style.configure(
            "FactERP.Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            font=("Arial", 9, "bold"),
        )

        cols = (
            "Número",
            "Fecha",
            "Cliente",
            "Términos",
            "Ref. Cliente",
            "Vencimiento",
            "Moneda",
            "Vendedor",
            "Monto",
        )
        self.tree = ttk.Treeview(
            tree_fr,
            columns=cols,
            show="headings",
            height=16,
            style="FactERP.Treeview",
        )
        wids = (100, 88, 200, 110, 110, 88, 72, 120, 90)
        for c, wi in zip(cols, wids):
            self.tree.heading(c, text=c)
            anchor = "center" if c != "Cliente" else "w"
            self.tree.column(c, width=wi, anchor=anchor)

        sb = ttk.Scrollbar(tree_fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda e: self._accion_ver_documento())

        foot = ctk.CTkFrame(tab_fact, fg_color="#334155", corner_radius=6)
        foot.pack(fill="x", padx=4, pady=(4, 6))

        inner = ctk.CTkFrame(foot, fg_color="transparent")
        inner.pack(fill="x", padx=6, pady=8)

        actions = [
            ("Crear", self._accion_crear, "#0d9488"),
            ("Modificar", self._accion_modificar, "#2563eb"),
            ("Imprimir", self._accion_imprimir, "#475569"),
            ("Anular", self._accion_anular, "#b91c1c"),
            ("Ver Docmto", self._accion_ver_documento, "#64748b"),
            ("Correo", self._accion_correo, "#64748b"),
            ("Cerrar", self._accion_cerrar, "#475569"),
            ("Pagar Doc.", self._accion_pagar, "#059669"),
            ("Notas", self._accion_notas, "#64748b"),
            ("Duplicar", self._accion_duplicar, "#ca8a04"),
            ("Salir", self._accion_salir, "#1e293b"),
        ]
        for txt, cmd, col in actions:
            ctk.CTkButton(
                inner,
                text=txt,
                width=92,
                height=52,
                font=("Arial", 10, "bold"),
                fg_color=col,
                hover_color="#1e293b",
                command=cmd,
            ).pack(side="left", padx=3, pady=2)

        self._cargar_lista()

    def _refrescar_panel_filtro(self):
        if not self.frame_filtro_dinamico:
            return
        for w in self.frame_filtro_dinamico.winfo_children():
            w.destroy()
        self._fw = {}
        row = ctk.CTkFrame(self.frame_filtro_dinamico, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=4)

        tipo = self.combo_tipo_filtro.get() if self.combo_tipo_filtro else "Fecha"
        hoy = datetime.now().date()

        def add_buttons():
            ctk.CTkButton(
                row,
                text="✓ Mostrar",
                width=100,
                height=28,
                fg_color="#059669",
                hover_color="#047857",
                command=self._mostrar_filtro,
            ).pack(side="left", padx=10, pady=4)
            ctk.CTkButton(
                row,
                text="✗ Limpiar",
                width=100,
                height=28,
                fg_color="#64748B",
                command=self._limpiar_filtro_actual,
            ).pack(side="left", padx=4, pady=4)

        if tipo == "Fecha":
            ctk.CTkLabel(row, text="Desde", font=("Arial", 10)).pack(
                side="left", padx=(4, 2)
            )
            self._fw["fd"] = ctk.CTkEntry(
                row, width=110, height=28, placeholder_text="DD/MM/AAAA"
            )
            self._fw["fd"].pack(side="left", padx=2)
            ctk.CTkLabel(row, text="Hasta", font=("Arial", 10)).pack(
                side="left", padx=(8, 2)
            )
            self._fw["fh"] = ctk.CTkEntry(
                row, width=110, height=28, placeholder_text="DD/MM/AAAA"
            )
            self._fw["fh"].pack(side="left", padx=2)
            self._fw["fd"].insert(0, (hoy - timedelta(days=30)).strftime("%d/%m/%Y"))
            self._fw["fh"].insert(0, hoy.strftime("%d/%m/%Y"))
        elif tipo == "Número documento":
            ctk.CTkLabel(row, text="Número", font=("Arial", 10)).pack(
                side="left", padx=(4, 2)
            )
            self._fw["num"] = ctk.CTkEntry(
                row, width=180, height=28, placeholder_text="Ej: 75673"
            )
            self._fw["num"].pack(side="left", padx=2)
        elif tipo == "Cliente":
            ctk.CTkLabel(row, text="Nombre / RNC", font=("Arial", 10)).pack(
                side="left", padx=(4, 2)
            )
            self._fw["cli"] = ctk.CTkEntry(row, width=280, height=28)
            self._fw["cli"].pack(side="left", padx=2)
        elif tipo == "Monto":
            ctk.CTkLabel(row, text="Mín.", font=("Arial", 10)).pack(
                side="left", padx=(4, 2)
            )
            self._fw["mmin"] = ctk.CTkEntry(row, width=96, height=28)
            self._fw["mmin"].pack(side="left", padx=2)
            ctk.CTkLabel(row, text="Máx.", font=("Arial", 10)).pack(
                side="left", padx=(8, 2)
            )
            self._fw["mmax"] = ctk.CTkEntry(row, width=96, height=28)
            self._fw["mmax"].pack(side="left", padx=2)
        elif tipo == "Producto":
            ctk.CTkLabel(row, text="Producto", font=("Arial", 10)).pack(
                side="left", padx=(4, 2)
            )
            self._fw["prod"] = ctk.CTkEntry(
                row, width=240, height=28, placeholder_text="Código o descripción"
            )
            self._fw["prod"].pack(side="left", padx=2)
        elif tipo == "Términos":
            self._fw["term"] = ctk.CTkComboBox(
                row,
                values=[
                    "Todos",
                    "En efectivo (saldado)",
                    "Pendiente / crédito",
                ],
                width=200,
                height=28,
            )
            self._fw["term"].set("Todos")
            self._fw["term"].pack(side="left", padx=4)
        elif tipo == "Bodega":
            bods = ["TODOS"] + (self.db.list_bodegas_codigos() or [])
            self._fw["bod"] = ctk.CTkComboBox(
                row, values=bods, width=160, height=28
            )
            self._fw["bod"].set("TODOS")
            self._fw["bod"].pack(side="left", padx=4)
        elif tipo == "Últimos N registros":
            ctk.CTkLabel(row, text="Cantidad", font=("Arial", 10)).pack(
                side="left", padx=(4, 2)
            )
            self._fw["n"] = ctk.CTkEntry(row, width=72, height=28)
            self._fw["n"].insert(0, "100")
            self._fw["n"].pack(side="left", padx=2)
        else:
            ctk.CTkLabel(
                row,
                text="Sin filtros adicionales (según estado arriba).",
                font=("Arial", 10),
                text_color="#94a3b8",
            ).pack(side="left", padx=8)

        add_buttons()

    def _parse_rango_desde_fw(self):
        e1 = self._fw.get("fd")
        e2 = self._fw.get("fh")
        if not e1 or not e2:
            return None
        t1 = (e1.get() or "").strip()
        t2 = (e2.get() or "").strip()
        if not t1 and not t2:
            return None
        d1 = _parse_fecha_entrada(t1)
        d2 = _parse_fecha_entrada(t2)
        if not d1 or not d2:
            return "bad"
        if d1 > d2:
            return "order"
        return (d1.isoformat(), d2.isoformat())

    @staticmethod
    def _parse_float_entry(s: str):
        s = (s or "").strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return "bad"

    def _estado_key(self) -> str:
        v = (self.combo_estado.get() or "").lower()
        if "anuladas" in v:
            return "anuladas"
        if "presupuesto" in v:
            return "presupuestos"
        if "emitidas" in v:
            return "emitidas"
        return "todos"

    def _modo_filtro_db(self) -> str:
        t = self.combo_tipo_filtro.get() if self.combo_tipo_filtro else ""
        return {
            "Fecha": "fecha",
            "Número documento": "numero",
            "Cliente": "cliente",
            "Monto": "monto",
            "Producto": "producto",
            "Términos": "terminos",
            "Bodega": "bodega",
            "Últimos N registros": "todos",
            "Todos (sin criterio extra)": "todos",
        }.get(t, "todos")

    def _mostrar_filtro(self):
        tipo = self.combo_tipo_filtro.get() if self.combo_tipo_filtro else ""
        if tipo == "Fecha":
            r = self._parse_rango_desde_fw()
            if r == "order":
                messagebox.showwarning(
                    "Fechas",
                    "La fecha inicial no puede ser posterior a la final.",
                    parent=self._toplevel(),
                )
                return
            if r == "bad":
                messagebox.showwarning(
                    "Fechas",
                    "Indique fecha desde y hasta (DD/MM/AAAA o AAAA-MM-DD).",
                    parent=self._toplevel(),
                )
                return
        elif tipo == "Número documento":
            n = (self._fw.get("num") and self._fw["num"].get() or "").strip()
            if not n:
                messagebox.showwarning(
                    "Número",
                    "Indique el número de documento.",
                    parent=self._toplevel(),
                )
                return
        elif tipo == "Cliente":
            c = (self._fw.get("cli") and self._fw["cli"].get() or "").strip()
            if not c:
                messagebox.showwarning(
                    "Cliente",
                    "Indique nombre o RNC a buscar.",
                    parent=self._toplevel(),
                )
                return
        elif tipo == "Monto":
            mn = self._parse_float_entry(
                self._fw.get("mmin") and self._fw["mmin"].get() or ""
            )
            mx = self._parse_float_entry(
                self._fw.get("mmax") and self._fw["mmax"].get() or ""
            )
            if mn == "bad" or mx == "bad":
                messagebox.showwarning(
                    "Monto",
                    "Montos inválidos. Use números (ej. 100 o 1000.50).",
                    parent=self._toplevel(),
                )
                return
        elif tipo == "Producto":
            p = (self._fw.get("prod") and self._fw["prod"].get() or "").strip()
            if not p:
                messagebox.showwarning(
                    "Producto",
                    "Indique texto a buscar en líneas de factura.",
                    parent=self._toplevel(),
                )
                return
        elif tipo == "Últimos N registros":
            ns = (self._fw.get("n") and self._fw["n"].get() or "").strip()
            if not ns.isdigit() or int(ns) < 1:
                messagebox.showwarning(
                    "Cantidad",
                    "Indique un entero mayor que cero.",
                    parent=self._toplevel(),
                )
                return
        self._cargar_lista()

    def _limpiar_filtro_actual(self):
        tipo = self.combo_tipo_filtro.get() if self.combo_tipo_filtro else ""
        hoy = datetime.now().date()
        if tipo == "Fecha" and self._fw.get("fd") and self._fw.get("fh"):
            self._fw["fd"].delete(0, "end")
            self._fw["fh"].delete(0, "end")
            self._fw["fd"].insert(0, (hoy - timedelta(days=30)).strftime("%d/%m/%Y"))
            self._fw["fh"].insert(0, hoy.strftime("%d/%m/%Y"))
        elif tipo == "Número documento" and self._fw.get("num"):
            self._fw["num"].delete(0, "end")
        elif tipo == "Cliente" and self._fw.get("cli"):
            self._fw["cli"].delete(0, "end")
        elif tipo == "Monto":
            if self._fw.get("mmin"):
                self._fw["mmin"].delete(0, "end")
            if self._fw.get("mmax"):
                self._fw["mmax"].delete(0, "end")
        elif tipo == "Producto" and self._fw.get("prod"):
            self._fw["prod"].delete(0, "end")
        elif tipo == "Términos" and self._fw.get("term"):
            self._fw["term"].set("Todos")
        elif tipo == "Bodega" and self._fw.get("bod"):
            self._fw["bod"].set("TODOS")
        elif tipo == "Últimos N registros" and self._fw.get("n"):
            self._fw["n"].delete(0, "end")
            self._fw["n"].insert(0, "100")
        self._cargar_lista()

    def _cargar_lista(self, numero_filtro: str | None = None):
        for i in self.tree.get_children():
            self.tree.delete(i)

        estado = self._estado_key()

        if numero_filtro is not None:
            rows = self.db.list_facturas_modulo_erp(
                estado_docs=estado,
                modo_filtro="numero",
                numero_buscar=numero_filtro,
                limit=5000,
            )
        else:
            tipo = self.combo_tipo_filtro.get() if self.combo_tipo_filtro else ""
            if tipo == "Últimos N registros":
                n = int((self._fw.get("n") and self._fw["n"].get() or "0").strip() or "0")
                n = max(1, n)
                rows = self.db.list_facturas_modulo_erp(
                    estado_docs=estado,
                    modo_filtro="todos",
                    ultimos_n=n,
                    limit=5000,
                )
            else:
                mf = self._modo_filtro_db()
                kw: dict = {
                    "estado_docs": estado,
                    "modo_filtro": mf,
                    "limit": 5000,
                }
                if mf == "fecha":
                    r = self._parse_rango_desde_fw()
                    if isinstance(r, tuple):
                        kw["fecha_desde"] = r[0]
                        kw["fecha_hasta"] = r[1]
                elif mf == "numero":
                    kw["numero_buscar"] = (
                        self._fw.get("num") and self._fw["num"].get() or ""
                    ).strip()
                elif mf == "cliente":
                    kw["cliente_buscar"] = (
                        self._fw.get("cli") and self._fw["cli"].get() or ""
                    ).strip()
                elif mf == "monto":
                    mn = self._parse_float_entry(
                        self._fw.get("mmin") and self._fw["mmin"].get() or ""
                    )
                    mx = self._parse_float_entry(
                        self._fw.get("mmax") and self._fw["mmax"].get() or ""
                    )
                    if mn not in (None, "bad"):
                        kw["monto_min"] = mn
                    if mx not in (None, "bad"):
                        kw["monto_max"] = mx
                elif mf == "producto":
                    kw["producto_buscar"] = (
                        self._fw.get("prod") and self._fw["prod"].get() or ""
                    ).strip()
                elif mf == "terminos":
                    lab = (
                        self._fw.get("term") and self._fw["term"].get() or "Todos"
                    )
                    if "efectivo" in lab.lower():
                        kw["terminos_modo"] = "efectivo"
                    elif "crédito" in lab.lower() or "credito" in lab.lower():
                        kw["terminos_modo"] = "credito"
                elif mf == "bodega":
                    b = (
                        self._fw.get("bod") and self._fw["bod"].get() or "TODOS"
                    ).strip()
                    if b.upper() not in ("TODOS", "TODAS"):
                        kw["bodega_filtro"] = b

                rows = self.db.list_facturas_modulo_erp(**kw)
        self.lbl_total_docs.configure(text=f"Total de documentos: {len(rows):,}")

        for row in rows:
            (
                fid,
                numero,
                fecha,
                ofrecido,
                ref_c,
                total,
                estado_doc,
                usuario,
                pagado,
                fvenc,
                moneda,
            ) = row
            total_f = float(total or 0)
            pagado_f = float(pagado or 0)
            fv = _fmt_fecha_factura(fecha)
            st = (estado_doc or "").lower()
            if st == "cotizacion":
                terminos_txt = "Presupuesto"
            else:
                terminos_txt = _terminos_pago(total_f, pagado_f)
            self.tree.insert(
                "",
                "end",
                iid=str(fid),
                values=(
                    _fmt_numero_display(numero),
                    fv,
                    (ofrecido or "")[:48],
                    terminos_txt,
                    ref_c or "—",
                    _fmt_vencimiento(fvenc),
                    moneda or "DOP",
                    usuario or "—",
                    _fmt_monto(total_f),
                ),
            )

    def _selected_id(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _dialogo_numero_documento(self, titulo: str) -> str | None:
        top = ctk.CTkToplevel(self._toplevel())
        top.title(titulo)
        top.geometry("340x160")
        top.transient(self._toplevel())
        top.grab_set()

        out = {"v": None}

        ctk.CTkLabel(top, text="Nro. Documento", font=("Arial", 12)).pack(
            padx=16, pady=(16, 4)
        )
        ent = ctk.CTkEntry(top, width=260, placeholder_text="Ej: 75676")
        ent.pack(padx=16, pady=4)
        ent.focus_set()

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=16)

        def ok():
            out["v"] = ent.get().strip()
            top.destroy()

        def cancel():
            top.destroy()

        ctk.CTkButton(bf, text="ACEPTAR", width=120, fg_color="#2563eb", command=ok).pack(
            side="left", padx=6
        )
        ctk.CTkButton(bf, text="CANCELAR", width=120, fg_color="#64748b", command=cancel).pack(
            side="left", padx=6
        )
        top.bind("<Return>", lambda e: ok())
        top.wait_window()
        return out["v"]

    def _buscar_y_seleccionar_por_numero(self, texto: str) -> bool:
        if not texto:
            return False
        self._cargar_lista(numero_filtro=texto)
        digits = "".join(c for c in texto if c.isdigit())
        for iid in self.tree.get_children():
            vals = self.tree.item(iid, "values")
            if not vals:
                continue
            nro = "".join(c for c in str(vals[0]) if c.isdigit())
            if digits and (nro.endswith(digits) or digits in nro):
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return True
        if len(self.tree.get_children()) == 1:
            self.tree.selection_set(self.tree.get_children()[0])
            return True
        return bool(self.tree.get_children())

    def _accion_crear(self):
        if getattr(self, "_pos_shell", None) is not None:
            return
        self.main.pack_forget()
        shell = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        shell.pack(fill="both", expand=True)

        bar = ctk.CTkFrame(shell, fg_color="#1e293b")
        bar.pack(fill="x", padx=6, pady=6)
        ctk.CTkButton(
            bar,
            text="← Volver al listado de facturas",
            width=220,
            fg_color="#475569",
            command=lambda: self._cerrar_punto_venta(shell),
        ).pack(side="left", padx=6, pady=6)
        ctk.CTkLabel(
            bar,
            text="Punto de venta — nueva factura",
            font=("Arial", 13, "bold"),
            text_color="white",
        ).pack(side="left", padx=12)

        body = ctk.CTkFrame(shell, fg_color="#2B2B2B")
        body.pack(fill="both", expand=True)
        FacturaManager(body, current_user=self.current_user)
        self._pos_shell = shell

    def _cerrar_punto_venta(self, shell):
        shell.destroy()
        self._pos_shell = None
        self.main.pack(fill="both", expand=True)
        self._cargar_lista()

    def _accion_modificar(self):
        messagebox.showinfo(
            "Modificar documento",
            "Por trazabilidad, las ventas emitidas no se editan como borrador. "
            "Para corregir montos o mercancía use la pestaña «Devoluciones» (nota de crédito y reingreso a inventario). "
            "Los presupuestos puede anularlos o duplicarlos y volver a cotizar desde el punto de venta.",
            parent=self._toplevel(),
        )

    def _accion_imprimir(self):
        fid = self._selected_id()
        if fid is None:
            r = self._dialogo_numero_documento("Imprimir factura")
            if r is None or not str(r).strip():
                return
            if not self._buscar_y_seleccionar_por_numero(r):
                messagebox.showwarning(
                    "Imprimir",
                    "No se encontró la factura.",
                    parent=self._toplevel(),
                )
                return
            fid = self._selected_id()
        if fid is None:
            return

        top = ctk.CTkToplevel(self._toplevel())
        top.title("Facturas")
        top.geometry("420x260")
        top.transient(self._toplevel())
        top.grab_set()
        ctk.CTkLabel(
            top,
            text="Enviar documento a:",
            font=("Arial", 12, "bold"),
        ).pack(pady=(12, 6), anchor="w", padx=16)

        var_dest = StringVar(value="vista_previa")
        frd = ctk.CTkFrame(top, fg_color="transparent")
        frd.pack(fill="x", padx=16)
        ctk.CTkRadioButton(
            frd,
            text="Vista previa",
            variable=var_dest,
            value="vista_previa",
            font=("Arial", 11),
        ).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(
            frd,
            text="Impresora",
            variable=var_dest,
            value="impresora",
            font=("Arial", 11),
        ).pack(anchor="w", pady=2)

        ctk.CTkLabel(top, text="Formato", font=("Arial", 11)).pack(
            anchor="w", padx=16, pady=(10, 2)
        )
        fmt = ctk.CTkComboBox(
            top,
            values=[
                "Ticket térmico (texto)",
                "PDF comprobante (A4)",
            ],
            width=300,
        )
        fmt.set("Ticket térmico (texto)")
        fmt.pack(padx=16, pady=4)

        def ejecutar():
            if "pdf" in (fmt.get() or "").lower():
                pdf_bytes = build_factura_comprobante_pdf(self.db, fid)
                if not pdf_bytes:
                    messagebox.showerror(
                        "PDF", "No se pudo generar el comprobante.", parent=top
                    )
                    return
                path = os.path.join(
                    tempfile.gettempdir(), f"comprobante_factura_{fid}.pdf"
                )
                with open(path, "wb") as f:
                    f.write(pdf_bytes)
                top.destroy()
                try:
                    if sys.platform.startswith("darwin"):
                        subprocess.run(["open", path], check=False)
                    elif sys.platform.startswith("win"):
                        os.startfile(path)  # type: ignore[attr-defined]
                    else:
                        subprocess.run(["xdg-open", path], check=False)
                except Exception as ex:
                    messagebox.showinfo(
                        "PDF",
                        f"Guardado en:\n{path}\nÁbralo manualmente.\n{ex}",
                        parent=self._toplevel(),
                    )
                return
            txt = self.db.generar_ticket_texto_factura(fid)
            if not txt:
                messagebox.showerror("Error", "No se pudo generar el ticket.", parent=top)
                return
            top.destroy()
            if var_dest.get() == "vista_previa":
                numero = self.tree.item(str(fid), "values")[0]
                win = ctk.CTkToplevel(self._toplevel())
                win.title(f"Vista previa — {numero}")
                win.geometry("440x520")
                tb = ctk.CTkTextbox(win, font=("Consolas", 11))
                tb.pack(fill="both", expand=True, padx=8, pady=8)
                tb.insert("1.0", txt)
                tb.configure(state="disabled")
            else:
                self._send_to_printer(txt)

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=16)
        ctk.CTkButton(
            bf, text="Aceptar", width=120, fg_color="#2563eb", command=ejecutar
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="Cancelar", width=100, fg_color="#64748b", command=top.destroy).pack(
            side="left", padx=8
        )

    def _send_to_printer(self, ticket_text: str):
        try:
            if sys.platform.startswith("win"):
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".txt", mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(ticket_text)
                    tmp_path = tmp.name
                subprocess.run(["notepad.exe", "/p", tmp_path], check=True)
            else:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".txt", mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(ticket_text)
                    tmp_path = tmp.name
                messagebox.showinfo(
                    "Impresión",
                    f"Ticket en:\n{tmp_path}\nÁbralo e imprima manualmente.",
                )
        except Exception as e:
            messagebox.showerror("Impresión", str(e))

    def _accion_anular(self):
        fid = self._selected_id()
        if fid is None:
            r = self._dialogo_numero_documento("Anular factura")
            if r is None or not str(r).strip():
                return
            if not self._buscar_y_seleccionar_por_numero(r):
                messagebox.showwarning(
                    "Anular",
                    "No se encontró el documento.",
                    parent=self._toplevel(),
                )
                return
            fid = self._selected_id()
        if fid is None:
            return
        res = self.db.get_factura_cobro_resumen(fid)
        if not res:
            return
        st = (res.get("estado") or "").lower()
        if st not in ("emitida", "cotizacion"):
            messagebox.showwarning(
                "Anular",
                "Solo pueden anularse documentos emitidos o presupuestos pendientes.",
                parent=self._toplevel(),
            )
            return

        top = ctk.CTkToplevel(self._toplevel())
        top.title("Anular documento")
        top.geometry("440x280")
        top.transient(self._toplevel())
        top.grab_set()

        ctk.CTkLabel(
            top,
            text="¿Anular este documento?",
            font=("Arial", 14, "bold"),
        ).pack(pady=(14, 4))
        ctk.CTkLabel(
            top,
            text=f"Documento: {res['numero']}  |  Total: RD$ {res['total']:,.2f}",
            font=("Arial", 11),
        ).pack(pady=4)
        ctk.CTkLabel(
            top,
            text="Ingrese el motivo de la anulación:",
            font=("Arial", 11),
        ).pack(anchor="w", padx=16, pady=(12, 4))

        tb = ctk.CTkTextbox(top, width=400, height=90, font=("Arial", 11))
        tb.pack(padx=16, pady=4)

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=16)

        def confirmar_si():
            motivo = tb.get("1.0", "end").strip()
            ok, msg = self.db.anular_factura(
                fid, motivo, self.current_user
            )
            if ok:
                messagebox.showinfo("Anulación", msg, parent=top)
                top.destroy()
                self._cargar_lista()
            else:
                messagebox.showwarning("Anulación", msg, parent=top)

        ctk.CTkButton(
            bf,
            text="Sí — Anular",
            width=130,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            command=confirmar_si,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bf,
            text="No",
            width=100,
            fg_color="#64748b",
            command=top.destroy,
        ).pack(side="left", padx=8)

    def _accion_ver_documento(self):
        fid = self._selected_id()
        if fid is None:
            messagebox.showwarning(
                "Ver documento",
                "Seleccione una factura en la lista.",
                parent=self._toplevel(),
            )
            return
        numero = self.tree.item(str(fid), "values")[0]
        txt = self.db.generar_ticket_texto_factura(fid)
        if not txt:
            messagebox.showerror("Error", "No se pudo cargar el documento.")
            return
        win = ctk.CTkToplevel(self._toplevel())
        win.title(f"Documento {numero}")
        win.geometry("440x520")
        tb = ctk.CTkTextbox(win, font=("Consolas", 11))
        tb.pack(fill="both", expand=True, padx=8, pady=8)
        tb.insert("1.0", txt)
        tb.configure(state="disabled")

    def _accion_correo(self):
        fid = self._selected_id()
        if fid is None:
            messagebox.showwarning(
                "Correo",
                "Seleccione un documento en la lista.",
                parent=self._toplevel(),
            )
            return
        em, _nom = self.db.get_factura_cliente_contacto(fid)
        if not em:
            messagebox.showinfo(
                "Correo electrónico",
                "No hay e-mail en el cliente de este documento. "
                "Actualice el maestro de clientes y vuelva a intentar.",
                parent=self._toplevel(),
            )
            return
        vals = self.tree.item(str(fid), "values")
        nro = vals[0] if vals else str(fid)
        q = urlencode(
            {
                "subject": f"Documento {nro}",
                "body": "Adjunto: puede generar el comprobante en PDF desde Imprimir "
                "en el módulo de facturación.\n",
            }
        )
        webbrowser.open(f"mailto:{em.strip()}?{q}")

    def _accion_cerrar(self):
        """Cierre de caja o turno (estilo MONICA)."""
        parent = self._toplevel()
        caja_row = self.db.fetch_caja_abierta_row()
        if caja_row is None:
            messagebox.showwarning(
                "Cierre de caja",
                "No hay un turno de caja abierto.\n"
                "Abra caja desde el módulo Caja antes de cerrar turno.",
                parent=parent,
            )
            return

        top = ctk.CTkToplevel(parent)
        top.title("Cierre de caja o turno")
        top.geometry("460x420")
        top.transient(parent)
        top.grab_set()

        ctk.CTkLabel(
            top,
            text="Si Ud. es la persona que va a operar la caja ingrese los siguientes datos:",
            font=("Arial", 11),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(14, 8))

        ctk.CTkLabel(top, text="Su nombre", font=("Arial", 11)).pack(anchor="w", padx=14)
        ent_nombre = ctk.CTkEntry(top, width=320, height=28)
        ent_nombre.pack(anchor="w", padx=14, pady=4)
        ent_nombre.insert(0, (self.current_user or "VENTA").strip() or "VENTA")

        ctk.CTkLabel(
            top,
            text="Dinero al finalizar (efectivo contado)",
            font=("Arial", 11),
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ent_dinero = ctk.CTkEntry(top, width=200, height=28)
        ent_dinero.pack(anchor="w", padx=14, pady=4)
        ent_dinero.insert(0, "0.00")

        hoy = datetime.now().strftime("%d/%m/%Y")
        ctk.CTkLabel(top, text="Fecha de cierre", font=("Arial", 11)).pack(
            anchor="w", padx=14, pady=(10, 2)
        )
        ent_fecha = ctk.CTkEntry(top, width=200, height=28)
        ent_fecha.pack(anchor="w", padx=14, pady=4)
        ent_fecha.insert(0, hoy)

        var_imprimir = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            top,
            text="Imprimir cierre",
            variable=var_imprimir,
            font=("Arial", 11),
        ).pack(anchor="w", padx=14, pady=(12, 4))

        ctk.CTkLabel(
            top,
            text="Observaciones (obligatorias si cierra con descuadre):",
            font=("Arial", 10),
            text_color="gray70",
        ).pack(anchor="w", padx=14, pady=(6, 2))
        tb_obs = ctk.CTkTextbox(top, width=400, height=56, font=("Arial", 10))
        tb_obs.pack(padx=14, pady=4)

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=14)

        def vista_previa_cierre():
            try:
                ef = float((ent_dinero.get() or "0").replace(",", ""))
            except ValueError:
                messagebox.showwarning(
                    "Cierre", "Indique un monto válido en «Dinero al finalizar».", parent=top
                )
                return
            txt = _texto_cierre_caja_turno(
                self.db,
                caja_row,
                ent_nombre.get().strip(),
                ef,
                ent_fecha.get().strip() or hoy,
            )
            pv = ctk.CTkToplevel(top)
            pv.title("Vista previa — cierre de caja")
            pv.geometry("480x420")
            tbx = ctk.CTkTextbox(pv, font=("Consolas", 11))
            tbx.pack(fill="both", expand=True, padx=8, pady=8)
            tbx.insert("1.0", txt)
            tbx.configure(state="disabled")

        def aceptar_cierre():
            try:
                efectivo_contado = float((ent_dinero.get() or "0").replace(",", ""))
            except ValueError:
                messagebox.showwarning(
                    "Cierre", "Dinero al finalizar no válido.", parent=top
                )
                return
            obs = tb_obs.get("1.0", "end").strip()
            ok, msg = ejecutar_cierre_caja_desde_fila(
                self.db,
                caja_row,
                efectivo_contado,
                obs if obs else None,
                usuario_cierre_display=ent_nombre.get().strip(),
                usuario_sesion=self.current_user,
                parent=top,
            )
            if not ok:
                if msg:
                    messagebox.showwarning("Cierre de caja", msg, parent=top)
                return
            if var_imprimir.get():
                txt_imp = _texto_cierre_caja_turno(
                    self.db,
                    caja_row,
                    ent_nombre.get().strip(),
                    efectivo_contado,
                    ent_fecha.get().strip() or hoy,
                )
                self._send_to_printer(txt_imp + "\n\n" + msg)
            messagebox.showinfo("Cierre de caja", msg, parent=top)
            top.destroy()

        ctk.CTkButton(
            bf,
            text="Aceptar",
            width=110,
            fg_color="#2563eb",
            command=aceptar_cierre,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            bf,
            text="Vista previa",
            width=110,
            fg_color="#64748b",
            command=vista_previa_cierre,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            bf,
            text="Salir",
            width=90,
            fg_color="#475569",
            command=top.destroy,
        ).pack(side="left", padx=6)

    def _dialogo_convertir_presupuesto(self, fid: int, res: dict):
        """Confirma presupuesto como venta: cobro total = suma de pagos, descuenta stock."""
        total = float(res.get("total") or 0)
        parent = self._toplevel()
        top = ctk.CTkToplevel(parent)
        top.title(f"Confirmar venta — {res.get('numero') or fid}")
        top.geometry("400x300")
        top.transient(parent)
        top.grab_set()

        ctk.CTkLabel(
            top,
            text="El presupuesto pasará a factura emitida. Los pagos deben cubrir el total.",
            font=("Arial", 11),
            wraplength=360,
        ).pack(padx=16, pady=(12, 4))
        ctk.CTkLabel(
            top,
            text=f"Total RD$ {_fmt_monto(total)}",
            font=("Arial", 15, "bold"),
            text_color="#f87171",
        ).pack(pady=6)

        frame_ef = ctk.CTkFrame(top, fg_color="transparent")
        frame_ef.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(frame_ef, text="Efectivo:").pack(side="left", padx=4)
        entry_ef = ctk.CTkEntry(frame_ef, width=120)
        entry_ef.pack(side="left", padx=4)
        entry_ef.insert(0, f"{total:.2f}")

        frame_tar = ctk.CTkFrame(top, fg_color="transparent")
        frame_tar.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(frame_tar, text="Tarjeta:").pack(side="left", padx=4)
        entry_tar = ctk.CTkEntry(frame_tar, width=120)
        entry_tar.pack(side="left", padx=4)
        entry_tar.insert(0, "0.00")

        frame_tr = ctk.CTkFrame(top, fg_color="transparent")
        frame_tr.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(frame_tr, text="Transferencia:").pack(side="left", padx=4)
        entry_tr = ctk.CTkEntry(frame_tr, width=120)
        entry_tr.pack(side="left", padx=4)
        entry_tr.insert(0, "0.00")

        def confirmar():
            try:
                ef = float((entry_ef.get() or "0").replace(",", ""))
                tar = float((entry_tar.get() or "0").replace(",", ""))
                trf = float((entry_tr.get() or "0").replace(",", ""))
            except ValueError:
                messagebox.showwarning("Pago", "Montos no válidos.", parent=top)
                return
            if ef < 0 or tar < 0 or trf < 0:
                messagebox.showwarning("Pago", "Use montos positivos.", parent=top)
                return
            pagos = []
            if ef > 0:
                pagos.append({"tipo": "efectivo", "monto": ef})
            if tar > 0:
                pagos.append({"tipo": "tarjeta", "monto": tar})
            if trf > 0:
                pagos.append({"tipo": "transferencia", "monto": trf})
            ok, msg = self.db.convertir_presupuesto_a_venta(
                fid, self.current_user, pagos
            )
            if ok:
                messagebox.showinfo("Venta confirmada", msg, parent=top)
                top.destroy()
                self._cargar_lista()
            else:
                messagebox.showwarning("Confirmar venta", msg, parent=top)

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=16)
        ctk.CTkButton(
            bf, text="Confirmar como venta", fg_color="#059669", command=confirmar
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="Cancelar", fg_color="#64748b", command=top.destroy).pack(
            side="left", padx=8
        )

    def _accion_pagar(self):
        fid = self._selected_id()
        if fid is None:
            r = self._dialogo_numero_documento("Cuentas por cobrar")
            if r is None or not str(r).strip():
                return
            self._cargar_lista()
            if not self._buscar_y_seleccionar_por_numero(r):
                messagebox.showwarning(
                    "Pagar documento",
                    "No se encontró la factura.",
                    parent=self._toplevel(),
                )
                return
            fid = self._selected_id()
        if fid is None:
            return

        res = self.db.get_factura_cobro_resumen(fid)
        if not res:
            return
        if (res.get("estado") or "").lower() == "cotizacion":
            self._dialogo_convertir_presupuesto(fid, res)
            return
        if res["balance"] <= 0:
            messagebox.showinfo(
                "Factura saldada",
                "Esta factura no tiene balance pendiente.",
                parent=self._toplevel(),
            )
            return

        top = ctk.CTkToplevel(self._toplevel())
        top.title(f"Cuentas por cobrar — Factura {res['numero']}")
        top.geometry("420x380")
        top.transient(self._toplevel())
        top.grab_set()

        ctk.CTkLabel(
            top,
            text=f"TOTAL (RD$): {_fmt_monto(res['total'])}",
            font=("Arial", 14, "bold"),
            text_color="#f87171",
        ).pack(pady=(12, 4))
        ctk.CTkLabel(
            top,
            text=f"Pagos previos: {_fmt_monto(res['pagado'])}",
            font=("Arial", 12),
            text_color="#4ade80",
        ).pack()
        ctk.CTkLabel(
            top,
            text=f"Balance: {_fmt_monto(res['balance'])}",
            font=("Arial", 13, "bold"),
        ).pack(pady=8)

        ctk.CTkLabel(top, text="Pago (RD$)", font=("Arial", 11)).pack(pady=(8, 2))
        ent_monto = ctk.CTkEntry(top, width=200, placeholder_text=str(res["balance"]))
        ent_monto.pack()
        if res["balance"] > 0:
            ent_monto.insert(0, f"{res['balance']:.2f}")

        ctk.CTkLabel(top, text="Forma de pago", font=("Arial", 11)).pack(pady=(10, 2))
        combo_fp = ctk.CTkComboBox(
            top,
            values=["efectivo", "tarjeta", "transferencia"],
            width=200,
        )
        combo_fp.set("efectivo")
        combo_fp.pack()

        ctk.CTkLabel(
            top,
            text="Recibo de ingreso — detalle extendido en la siguiente fase.",
            font=("Arial", 10),
            text_color="gray",
            wraplength=380,
        ).pack(pady=12)

        def aceptar():
            try:
                m = float((ent_monto.get() or "0").replace(",", ""))
            except ValueError:
                messagebox.showwarning("Pago", "Monto no válido.", parent=top)
                return
            ok, msg = self.db.registrar_pago_factura(
                fid, combo_fp.get().strip() or "efectivo", m
            )
            if ok:
                messagebox.showinfo("Pago", msg, parent=top)
                top.destroy()
                self._cargar_lista()
            else:
                messagebox.showwarning("Pago", msg, parent=top)

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=16)
        ctk.CTkButton(
            bf, text="ACEPTAR", width=120, fg_color="#059669", command=aceptar
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="CANCELAR", width=120, fg_color="#64748b", command=top.destroy).pack(
            side="left", padx=8
        )

    def _accion_notas(self):
        fid = self._selected_id()
        if fid is None:
            messagebox.showwarning(
                "Notas",
                "Seleccione un documento en la lista.",
                parent=self._toplevel(),
            )
            return
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT IFNULL(observaciones,''), IFNULL(referencia_entrega,'')
            FROM facturas WHERE id = ?
            """,
            (fid,),
        )
        row = cur.fetchone()
        conn.close()
        obs0, ref0 = row if row else ("", "")

        parent = self._toplevel()
        top = ctk.CTkToplevel(parent)
        top.title("Notas al documento")
        top.geometry("440x360")
        top.transient(parent)
        top.grab_set()

        ctk.CTkLabel(top, text="Observaciones (internas / pie de documento)", font=("Arial", 11)).pack(
            anchor="w", padx=14, pady=(12, 2)
        )
        tb_o = ctk.CTkTextbox(top, height=100, font=("Arial", 11))
        tb_o.pack(fill="x", padx=14, pady=4)
        tb_o.insert("1.0", obs0)

        ctk.CTkLabel(top, text="Referencia de entrega / OC cliente", font=("Arial", 11)).pack(
            anchor="w", padx=14, pady=(8, 2)
        )
        tb_r = ctk.CTkTextbox(top, height=56, font=("Arial", 11))
        tb_r.pack(fill="x", padx=14, pady=4)
        tb_r.insert("1.0", ref0)

        def guardar():
            ok = self.db.actualizar_factura_notas(
                fid,
                tb_o.get("1.0", "end"),
                tb_r.get("1.0", "end"),
            )
            if ok:
                messagebox.showinfo("Notas", "Cambios guardados.", parent=top)
                top.destroy()
                self._cargar_lista()
            else:
                messagebox.showwarning("Notas", "No se pudo actualizar.", parent=top)

        bf = ctk.CTkFrame(top, fg_color="transparent")
        bf.pack(pady=14)
        ctk.CTkButton(bf, text="Guardar", fg_color="#2563eb", command=guardar).pack(
            side="left", padx=8
        )
        ctk.CTkButton(bf, text="Cerrar", fg_color="#64748b", command=top.destroy).pack(
            side="left", padx=8
        )

    def _accion_duplicar(self):
        if getattr(self, "_pos_shell", None) is not None:
            return
        fid = self._selected_id()
        if fid is None:
            r = self._dialogo_numero_documento("Duplicar factura")
            if r is None or not str(r).strip():
                return
            if not self._buscar_y_seleccionar_por_numero(r):
                messagebox.showwarning(
                    "Duplicar",
                    "No se encontró el documento.",
                    parent=self._toplevel(),
                )
                return
            fid = self._selected_id()
        if fid is None:
            return
        res = self.db.get_factura_cobro_resumen(fid)
        if not res:
            return
        st = (res.get("estado") or "").lower()
        if st not in ("emitida", "cotizacion"):
            messagebox.showwarning(
                "Duplicar",
                "Solo se puede duplicar un documento emitido o un presupuesto.",
                parent=self._toplevel(),
            )
            return
        if not self.db.get_factura_para_duplicar(fid):
            messagebox.showwarning(
                "Duplicar",
                "No hay líneas duplicables (cada línea debe tener producto en inventario).",
                parent=self._toplevel(),
            )
            return

        self.main.pack_forget()
        shell = ctk.CTkFrame(self.parent, fg_color="#2B2B2B")
        shell.pack(fill="both", expand=True)

        bar = ctk.CTkFrame(shell, fg_color="#1e293b")
        bar.pack(fill="x", padx=6, pady=6)
        ctk.CTkButton(
            bar,
            text="← Volver al listado de facturas",
            width=220,
            fg_color="#475569",
            command=lambda: self._cerrar_punto_venta(shell),
        ).pack(side="left", padx=6, pady=6)
        ctk.CTkLabel(
            bar,
            text=f"Punto de venta — duplicado desde {res['numero']}",
            font=("Arial", 13, "bold"),
            text_color="white",
        ).pack(side="left", padx=12)

        body = ctk.CTkFrame(shell, fg_color="#2B2B2B")
        body.pack(fill="both", expand=True)
        FacturaManager(
            body,
            current_user=self.current_user,
            duplicar_desde_factura_id=fid,
        )
        self._pos_shell = shell

    def _accion_salir(self):
        if self.app is not None:
            self.app.show_dashboard()
