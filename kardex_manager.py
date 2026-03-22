"""Vista Kardex (movimientos por producto) estilo ERP."""

import csv
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from movimiento_inventario_dialogs import RecibirProductoDialog, RetirarProductoDialog
from producto_dialog import ProductoDialog
from styles import Styles


def _fmt_fecha_mov(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s[:16]


def _rango_desde_combo(etiqueta: str):
    """Devuelve (fecha_desde, fecha_hasta) como 'YYYY-MM-DD' o (None, None)."""
    hoy = datetime.now().date()
    if not etiqueta or etiqueta == "Todos" or etiqueta == "Personalizado":
        return None, None
    if etiqueta == "Últimos 7 días":
        return (hoy - timedelta(days=7)).isoformat(), hoy.isoformat()
    if etiqueta == "Últimos 15 días":
        return (hoy - timedelta(days=15)).isoformat(), hoy.isoformat()
    if etiqueta == "Últimos 30 días" or etiqueta == "Últimos 1 mes":
        return (hoy - timedelta(days=30)).isoformat(), hoy.isoformat()
    if etiqueta == "Este mes":
        d1 = hoy.replace(day=1)
        return d1.isoformat(), hoy.isoformat()
    if etiqueta == "Este año":
        d1 = hoy.replace(month=1, day=1)
        return d1.isoformat(), hoy.isoformat()
    return None, None


class KardexPanel(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        db,
        image_manager,
        *,
        current_user=None,
        on_refresh_products=None,
    ):
        super().__init__(parent, **Styles.get_frame_style())
        self.db = db
        self.image_manager = image_manager
        self.current_user = current_user
        self.on_refresh_products = on_refresh_products
        self.producto_id = None
        self._ids_nav = self.db.list_ids_productos_activos()
        self._build_ui()

    def _toplevel_for_dialogs(self):
        # No usar el nombre _root(): en Tkinter eso pisa el método interno y provoca recursión infinita.
        w = self
        while getattr(w, "master", None) is not None:
            w = w.master
        return w

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 4))

        row1 = ctk.CTkFrame(top, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkLabel(row1, text="Producto:", font=("Arial", 11, "bold")).pack(
            side="left", padx=(0, 6)
        )
        self.ent_codigo = ctk.CTkEntry(
            row1, placeholder_text="Código, nombre o ID…", width=220, height=28
        )
        self.ent_codigo.pack(side="left", padx=4)
        self.ent_codigo.bind("<Return>", lambda e: self._buscar())
        ctk.CTkButton(row1, text="Buscar", width=80, height=28, command=self._buscar).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            row1,
            text="Todos",
            width=70,
            height=28,
            fg_color="#64748B",
            command=self._limpiar_producto,
        ).pack(side="left", padx=4)

        ctk.CTkLabel(row1, text="En bodega:", font=("Arial", 11)).pack(
            side="left", padx=(16, 6)
        )
        bods = ["TODOS"] + self.db.list_bodegas_codigos()
        self.combo_bodega = ctk.CTkComboBox(row1, values=bods, width=140, height=28)
        self.combo_bodega.set("TODOS")
        self.combo_bodega.pack(side="left", padx=4)

        ctk.CTkButton(
            row1,
            text="✓ Mostrar",
            width=100,
            height=28,
            fg_color="#059669",
            command=self._mostrar,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            row1,
            text="Cancelar",
            width=90,
            height=28,
            fg_color="#6B7280",
            command=self._cancelar_filtros,
        ).pack(side="left", padx=4)

        self.lbl_nombre_prod = ctk.CTkLabel(
            top,
            text="",
            font=("Arial", 12, "bold"),
            text_color="#22C55E",
            anchor="w",
        )
        self.lbl_nombre_prod.pack(fill="x", padx=4, pady=(4, 0))

        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(fill="x", padx=10, pady=8)

        self._lbl_resumen = {}
        labels = [
            ("producto", "Producto:"),
            ("codigo", "Código:"),
            ("stock", "Total productos:"),
            ("uv", "Unids. vendidas:"),
            ("costo", "Costo $:"),
            ("precio", "Precio 1 $:"),
            ("cat", "Categoría:"),
            ("min", "Cant. mínima:"),
        ]
        for i, (key, lab) in enumerate(labels):
            r, c = divmod(i, 2)
            fr = ctk.CTkFrame(mid, fg_color="transparent")
            fr.grid(row=r, column=c, sticky="w", padx=12, pady=4)
            ctk.CTkLabel(fr, text=lab, font=("Arial", 10)).pack(side="left")
            vl = ctk.CTkLabel(fr, text="—", font=("Arial", 10, "bold"), text_color="#22C55E")
            vl.pack(side="left", padx=(6, 0))
            self._lbl_resumen[key] = vl

        right = ctk.CTkFrame(mid, fg_color="transparent")
        right.grid(row=0, column=2, rowspan=4, sticky="ne", padx=20)
        self.combo_periodo = ctk.CTkComboBox(
            right,
            values=[
                "Todos",
                "Últimos 7 días",
                "Últimos 15 días",
                "Últimos 30 días",
                "Este mes",
                "Este año",
                "Personalizado",
            ],
            width=160,
            height=28,
            command=self._on_periodo_changed,
        )
        self.combo_periodo.set("Últimos 30 días")
        self.combo_periodo.pack(anchor="e", pady=4)

        self._frm_custom_dates = ctk.CTkFrame(right, fg_color="transparent")
        self._frm_custom_dates.pack(anchor="e", pady=(2, 0))
        ctk.CTkLabel(
            self._frm_custom_dates, text="Desde", font=("Arial", 9)
        ).pack(side="left", padx=(0, 4))
        self.ent_fecha_desde = ctk.CTkEntry(
            self._frm_custom_dates,
            width=108,
            height=26,
            placeholder_text="AAAA-MM-DD",
        )
        self.ent_fecha_desde.pack(side="left", padx=2)
        ctk.CTkLabel(
            self._frm_custom_dates, text="Hasta", font=("Arial", 9)
        ).pack(side="left", padx=(6, 4))
        self.ent_fecha_hasta = ctk.CTkEntry(
            self._frm_custom_dates,
            width=108,
            height=26,
            placeholder_text="AAAA-MM-DD",
        )
        self.ent_fecha_hasta.pack(side="left", padx=2)
        self._frm_custom_dates.pack_forget()

        ctk.CTkLabel(
            right,
            text="Cálculo del costeo: Promedio ponderado",
            font=("Arial", 9),
            text_color="#DC2626",
        ).pack(anchor="e", pady=(4, 0))

        tree_frame = ctk.CTkFrame(self, fg_color="transparent")
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self.lbl_kardex_hint = ctk.CTkLabel(
            tree_frame,
            text="",
            font=("Arial", 11),
            text_color="#F59E0B",
            anchor="w",
        )
        self.lbl_kardex_hint.pack(fill="x", padx=4, pady=(0, 4))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "Kardex.Treeview",
            background="#2a2d2e",
            foreground="white",
            rowheight=24,
            fieldbackground="#2a2d2e",
        )
        style.configure(
            "Kardex.Treeview.Heading",
            background="#3B3B3B",
            foreground="white",
            font=("Arial", 9, "bold"),
        )

        cols = (
            "Fecha",
            "Descripción",
            "Tipo",
            "Empresa",
            "Bodega",
            "Unidades",
            "Balance",
            "Precio $",
        )
        self.tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            height=14,
            style="Kardex.Treeview",
        )
        w = (88, 260, 44, 140, 100, 72, 72, 80)
        for col, wi in zip(cols, w):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=wi, anchor="center")
        self.tree.column("Descripción", anchor="w")
        self.tree.column("Empresa", anchor="w")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        foot = ctk.CTkFrame(self, fg_color=("#E8EDF3", "#252525"))
        foot.pack(fill="x", padx=6, pady=(4, 8))
        inner = ctk.CTkFrame(foot, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=8)

        actions = [
            ("Recibir\nprods.", self._recibir, "#059669"),
            ("Retirar", self._retirar, "#2563EB"),
            ("Modificar", self._modificar, "#CA8A04"),
            ("Reporte\nCSV", self._export_csv, "#475569"),
            ("Prod.\nanterior", self._nav_prev, "#64748B"),
            ("Sgte.\nproducto", self._nav_next, "#64748B"),
        ]
        for txt, cmd, col in actions:
            ctk.CTkButton(
                inner,
                text=txt,
                width=92,
                height=68,
                font=("Arial", 10, "bold"),
                fg_color=col,
                hover_color="#1e293b",
                command=cmd,
            ).pack(side="left", padx=4, pady=2)

    @staticmethod
    def _parse_fecha_entry(s: str):
        s = (s or "").strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _on_periodo_changed(self, choice=None):
        val = choice if choice is not None else self.combo_periodo.get()
        if val == "Personalizado":
            self._frm_custom_dates.pack(anchor="e", pady=(2, 0))
        else:
            self._frm_custom_dates.pack_forget()

    def _fechas_filtro(self):
        lab = self.combo_periodo.get()
        if lab == "Personalizado":
            d1 = self._parse_fecha_entry(self.ent_fecha_desde.get())
            d2 = self._parse_fecha_entry(self.ent_fecha_hasta.get())
            if d1 is None or d2 is None:
                messagebox.showwarning(
                    "Kardex",
                    "Indique fechas válidas (AAAA-MM-DD o DD/MM/AAAA) en Desde y Hasta.",
                    parent=self._toplevel_for_dialogs(),
                )
                return False, None, None
            if d1 > d2:
                messagebox.showwarning(
                    "Kardex",
                    "La fecha inicial no puede ser posterior a la final.",
                    parent=self._toplevel_for_dialogs(),
                )
                return False, None, None
            return True, d1.isoformat(), d2.isoformat()
        fd, fh = _rango_desde_combo(lab)
        return True, fd, fh

    def set_product(self, producto_id: int):
        self.producto_id = int(producto_id)
        self._ids_nav = self.db.list_ids_productos_activos()
        res = self.db.get_producto_kardex_resumen(self.producto_id)
        if res:
            self.ent_codigo.delete(0, "end")
            self.ent_codigo.insert(0, res["codigo"])
            self.lbl_nombre_prod.configure(text=res["nombre"][:120])
        self._refrescar_resumen()
        self._mostrar()

    def _limpiar_producto(self):
        self.producto_id = None
        self.ent_codigo.delete(0, "end")
        self.lbl_nombre_prod.configure(text="")
        if getattr(self, "lbl_kardex_hint", None):
            self.lbl_kardex_hint.configure(text="")
        for k in self._lbl_resumen:
            self._lbl_resumen[k].configure(text="—")
        self.tree.delete(*self.tree.get_children())

    def _buscar(self):
        cod = (self.ent_codigo.get() or "").strip()
        if not cod:
            messagebox.showwarning(
                "Kardex",
                "Escriba código, nombre o ID del producto.",
                parent=self._toplevel_for_dialogs(),
            )
            return
        pid = self.db.buscar_primer_producto_por_codigo(cod)
        if not pid:
            messagebox.showinfo(
                "Kardex",
                "No se encontró un producto activo con ese criterio.\n"
                "Pruebe con el código (ej. DEMO-AGUA-01) o parte del nombre.",
                parent=self._toplevel_for_dialogs(),
            )
            return
        self.producto_id = pid
        res = self.db.get_producto_kardex_resumen(pid)
        if res:
            self.lbl_nombre_prod.configure(text=res["nombre"][:120])
        self._refrescar_resumen()
        self._mostrar()

    def _cancelar_filtros(self):
        self.combo_bodega.set("TODOS")
        self.combo_periodo.set("Todos")
        self.ent_fecha_desde.delete(0, "end")
        self.ent_fecha_hasta.delete(0, "end")
        self._frm_custom_dates.pack_forget()
        self._mostrar()

    def _mostrar(self):
        if not self.producto_id:
            cod = (self.ent_codigo.get() or "").strip()
            if cod:
                pid = self.db.buscar_primer_producto_por_codigo(cod)
                if pid:
                    self.producto_id = pid
                    res = self.db.get_producto_kardex_resumen(pid)
                    if res:
                        self.lbl_nombre_prod.configure(text=res["nombre"][:120])
                        self.ent_codigo.delete(0, "end")
                        self.ent_codigo.insert(0, (res.get("codigo") or "") or str(pid))
                    self._refrescar_resumen()
                else:
                    messagebox.showinfo(
                        "Kardex",
                        "No hay producto seleccionado o no se reconoce el texto.\n"
                        "Use código o nombre y pulse Buscar o Mostrar.",
                        parent=self._toplevel_for_dialogs(),
                    )
                    return
            else:
                messagebox.showinfo(
                    "Kardex",
                    "Escriba código o nombre del producto y pulse Buscar o Mostrar.",
                    parent=self._toplevel_for_dialogs(),
                )
                return
        ok, fd, fh = self._fechas_filtro()
        if not ok:
            return
        bod = self.combo_bodega.get()
        rows = self.db.get_kardex_filas_con_saldo(
            self.producto_id,
            fecha_desde=fd,
            fecha_hasta=fh,
            bodega_filtro=bod,
        )
        hint_parts = []
        if rows:
            sum_in = sum(r["cantidad"] for r in rows if r["cantidad"] > 0)
            sum_out = sum(r["cantidad"] for r in rows if r["cantidad"] < 0)
            hint_parts.append(
                f"Periodo: +{sum_in:.2f} u. entradas, {sum_out:.2f} u. salidas netas "
                f"({len(rows)} mov.)"
            )
        if (
            not rows
            and self.producto_id
            and bod
            and bod.strip().upper() not in ("TODOS", "TODAS", "")
        ):
            rows_todas = self.db.get_kardex_filas_con_saldo(
                self.producto_id,
                fecha_desde=fd,
                fecha_hasta=fh,
                bodega_filtro="TODOS",
            )
            if rows_todas:
                hint_parts.append(
                    f'Sin movimientos en bodega «{bod}» en este periodo. '
                    f'Use «TODOS» para ver otras bodegas.'
                )
        self.lbl_kardex_hint.configure(
            text=" · ".join(hint_parts) if hint_parts else ""
        )
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            u = r["cantidad"]
            u_s = f"{u:,.2f}" if u == int(u) else f"{u:.2f}"
            bal = r["balance"]
            bal_s = f"{bal:,.2f}" if bal == int(bal) else f"{bal:.2f}"
            pr = r.get("precio")
            pr_s = "" if pr is None else f"{float(pr):,.2f}"
            self.tree.insert(
                "",
                "end",
                values=(
                    _fmt_fecha_mov(r["fecha"]),
                    (r["descripcion"] or "")[:200],
                    r.get("tipo_codigo") or "",
                    (r.get("entidad") or "")[:80],
                    r.get("bodega") or "",
                    u_s,
                    bal_s,
                    pr_s,
                ),
            )

    def _refrescar_resumen(self):
        if not self.producto_id:
            return
        res = self.db.get_producto_kardex_resumen(self.producto_id)
        if not res:
            return
        self._lbl_resumen["producto"].configure(text=(res["nombre"] or "")[:56])
        self._lbl_resumen["codigo"].configure(text=res["codigo"] or "")
        self._lbl_resumen["stock"].configure(text=f"{res['stock']:.2f}")
        self._lbl_resumen["uv"].configure(text=f"{res['unids_vendidas']:.2f}")
        self._lbl_resumen["costo"].configure(text=f"{res['costo']:.2f}")
        self._lbl_resumen["precio"].configure(text=f"{res['precio']:.2f}")
        self._lbl_resumen["cat"].configure(text=res["categoria"] or "—")
        self._lbl_resumen["min"].configure(text=f"{res['stock_minimo']:.2f}")

    def _recibir(self):
        if not self.producto_id:
            messagebox.showwarning("Kardex", "Seleccione un producto.", parent=self._toplevel_for_dialogs())
            return
        res = self.db.get_producto_kardex_resumen(self.producto_id)
        if not res:
            return
        bods = self.db.list_bodegas_codigos()
        RecibirProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.producto_id,
            nombre_producto=res["nombre"],
            bodegas=bods,
            bodega_default=res.get("bodega_default") or (bods[0] if bods else ""),
            usuario=self.current_user,
            on_done=self._after_movimiento,
        )

    def _retirar(self):
        if not self.producto_id:
            messagebox.showwarning("Kardex", "Seleccione un producto.", parent=self._toplevel_for_dialogs())
            return
        res = self.db.get_producto_kardex_resumen(self.producto_id)
        if not res:
            return
        bods = self.db.list_bodegas_codigos()
        RetirarProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.producto_id,
            nombre_producto=res["nombre"],
            bodegas=bods,
            bodega_default=res.get("bodega_default") or (bods[0] if bods else ""),
            stock_actual=res["stock"],
            usuario=self.current_user,
            on_done=self._after_movimiento,
        )

    def _after_movimiento(self):
        self._refrescar_resumen()
        self._mostrar()
        if self.on_refresh_products:
            self.on_refresh_products()

    def _modificar(self):
        if not self.producto_id:
            messagebox.showwarning("Kardex", "Seleccione un producto.", parent=self._toplevel_for_dialogs())
            return
        ProductoDialog(
            self._toplevel_for_dialogs(),
            self.db,
            self.image_manager,
            producto_id=self.producto_id,
            read_only=False,
            on_saved=self._after_movimiento,
        )

    def _export_csv(self):
        if not self.producto_id:
            messagebox.showwarning("Kardex", "Seleccione un producto.", parent=self._toplevel_for_dialogs())
            return
        res = self.db.get_producto_kardex_resumen(self.producto_id)
        cod = (res or {}).get("codigo") or str(self.producto_id)
        path = filedialog.asksaveasfilename(
            parent=self._toplevel_for_dialogs(),
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"kardex_{cod}_{datetime.now().strftime('%Y%m%d')}.csv",
        )
        if not path:
            return
        fd, fh = _rango_desde_combo(self.combo_periodo.get())
        bod = self.combo_bodega.get()
        rows = self.db.get_kardex_filas_con_saldo(
            self.producto_id,
            fecha_desde=fd,
            fecha_hasta=fh,
            bodega_filtro=bod,
        )
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(
                    [
                        "Fecha",
                        "Descripción",
                        "Tipo",
                        "Empresa",
                        "Bodega",
                        "Unidades",
                        "Balance",
                        "Precio",
                    ]
                )
                for r in rows:
                    w.writerow(
                        [
                            _fmt_fecha_mov(r["fecha"]),
                            r["descripcion"],
                            r.get("tipo_codigo"),
                            r.get("entidad"),
                            r.get("bodega"),
                            r["cantidad"],
                            r["balance"],
                            r.get("precio"),
                        ]
                    )
            messagebox.showinfo("Kardex", f"Exportado: {path}", parent=self._toplevel_for_dialogs())
        except OSError as e:
            messagebox.showerror("Kardex", str(e), parent=self._toplevel_for_dialogs())

    def _nav_prev(self):
        if not self.producto_id or not self._ids_nav:
            return
        try:
            i = self._ids_nav.index(self.producto_id)
        except ValueError:
            self._ids_nav = self.db.list_ids_productos_activos()
            try:
                i = self._ids_nav.index(self.producto_id)
            except ValueError:
                return
        if i > 0:
            self.set_product(self._ids_nav[i - 1])

    def _nav_next(self):
        if not self.producto_id or not self._ids_nav:
            return
        try:
            i = self._ids_nav.index(self.producto_id)
        except ValueError:
            self._ids_nav = self.db.list_ids_productos_activos()
            try:
                i = self._ids_nav.index(self.producto_id)
            except ValueError:
                return
        if i < len(self._ids_nav) - 1:
            self.set_product(self._ids_nav[i + 1])
