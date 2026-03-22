"""
Ventana «producto» (crear/editar): diseño tipo pantalla de inventario ERP.
Se abre desde Inventario → Crear producto / Modificar / Ver.
"""
import os
from tkinter import messagebox

import customtkinter as ctk

from database import Database
from image_manager import ImageManager
from modern_image_selector import ModernImageSelector

# Otros impuestos opcionales (sobre precio base, sumados al ITBIS si ambos aplican en ventas)
OTROS_IMPUESTO_LABELS = ["Ninguno", "ISC 10%", "CDT 2%"]
OTROS_IMPUESTO_TASA = {"Ninguno": 0.0, "ISC 10%": 0.10, "CDT 2%": 0.02}

# ITBIS RD — precio en catálogo = base sin ITBIS
ITBIS_TASA = 0.18


class _ListaSeleccionDialog(ctk.CTkToplevel):
    def __init__(self, master, title, rows, fmt_row, *, db=None, allow_create=False):
        super().__init__(master)
        self.title(title)
        self.geometry("480x360")
        self.seleccion = None
        self._db = db
        self._allow_create = allow_create
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(self, text="Seleccione:").pack(anchor="w", padx=10, pady=(10, 4))
        sf = ctk.CTkScrollableFrame(self, height=240)
        sf.pack(fill="both", expand=True, padx=10, pady=4)

        for r in rows:
            txt = fmt_row(r)
            ctk.CTkButton(
                sf,
                text=txt[:80] + ("…" if len(txt) > 80 else ""),
                anchor="w",
                command=lambda row=r: self._elegir(row),
            ).pack(fill="x", pady=2)

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", pady=10)
        if allow_create and db:
            ctk.CTkButton(
                bf, text="Nuevo proveedor", command=self._nuevo_prov
            ).pack(side="left", padx=8)
        ctk.CTkButton(
            bf, text="Cancelar", fg_color="#6B7280", command=self.destroy
        ).pack(side="right", padx=8)

    def _elegir(self, row):
        self.seleccion = row
        self.destroy()

    def _nuevo_prov(self):
        from tkinter import simpledialog

        n = simpledialog.askstring("Proveedor", "Nombre del proveedor:", parent=self)
        if not n or not n.strip():
            return
        try:
            nid = self._db.crear_proveedor(n.strip())
            self.seleccion = (nid, n.strip(), None, None)
            self.destroy()
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)


class _MultiSeleccionDialog(ctk.CTkToplevel):
    def __init__(self, master, title, rows, fmt_row, preselected, *, id_index=0):
        super().__init__(master)
        self.title(title)
        self.geometry("520x400")
        self.resultado = None
        self._checks = []
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(self, text="Marque las filas deseadas:").pack(
            anchor="w", padx=10, pady=(10, 4)
        )
        sf = ctk.CTkScrollableFrame(self, height=280)
        sf.pack(fill="both", expand=True, padx=10, pady=4)

        pre = set(preselected or [])
        for r in rows:
            rid = r[id_index]
            var = ctk.BooleanVar(value=rid in pre)
            txt = fmt_row(r)
            ctk.CTkCheckBox(sf, text=txt[:90], variable=var).pack(anchor="w", pady=3)
            self._checks.append((rid, var))

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(pady=10)
        ctk.CTkButton(bf, text="Aceptar", command=self._ok).pack(side="left", padx=6)
        ctk.CTkButton(
            bf, text="Cancelar", fg_color="#6B7280", command=self.destroy
        ).pack(side="left", padx=6)

    def _ok(self):
        self.resultado = {rid for rid, var in self._checks if var.get()}
        self.destroy()


class _ComponentesDialog(ctk.CTkToplevel):
    def __init__(self, master, db: Database, producto_id, rows_actual):
        super().__init__(master)
        self.title("Componentes del producto")
        self.geometry("560x420")
        self.db = db
        self.producto_id = producto_id
        self.rows = list(rows_actual or [])
        self.guardado = False
        self._entries = []
        self.transient(master)
        self.grab_set()

        mapa = {int(a[0]): float(a[1]) for a in self.rows}
        excl = producto_id if producto_id else None
        prods = db.list_productos_picker(exclude_id=excl)

        ctk.CTkLabel(
            self, text="Cantidad por componente (0 = no incluir):"
        ).pack(anchor="w", padx=10, pady=(10, 4))
        sf = ctk.CTkScrollableFrame(self, height=300)
        sf.pack(fill="both", expand=True, padx=10, pady=4)

        for pid, cod, nom in prods:
            f = ctk.CTkFrame(sf, fg_color="transparent")
            f.pack(fill="x", pady=2)
            ctk.CTkLabel(
                f, text=f"{cod or '—'}  {nom[:40]}", width=280, anchor="w"
            ).pack(side="left", padx=4)
            e = ctk.CTkEntry(f, width=56, height=26)
            e.pack(side="right", padx=4)
            q = mapa.get(int(pid), 0.0)
            e.insert(0, f"{q:g}" if q else "0")
            self._entries.append((int(pid), e))

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(pady=10)
        ctk.CTkButton(bf, text="Guardar", command=self._ok).pack(side="left", padx=6)
        ctk.CTkButton(
            bf, text="Cancelar", fg_color="#6B7280", command=self.destroy
        ).pack(side="left", padx=6)

    def _ok(self):
        out = []
        for pid, e in self._entries:
            try:
                q = float((e.get() or "0").replace(",", "."))
            except ValueError:
                q = 0.0
            if q > 0:
                out.append((pid, q))
        self.rows = out
        self.guardado = True
        self.destroy()


class ProductoDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        db: Database,
        image_manager: ImageManager,
        *,
        product_id=None,
        read_only=False,
        on_saved=None,
        duplicate_from_id=None,
    ):
        super().__init__(master)
        self.db = db
        self.image_manager = image_manager
        self.product_id = product_id
        self.read_only = read_only
        self.on_saved = on_saved
        self._duplicate_src = duplicate_from_id
        self._is_new = product_id is None and duplicate_from_id is None
        self._loaded_img_path = None
        self._tipo_radios = []
        self._proveedor_id = None
        self._proveedores_sec_ids = []
        self._componentes_rows = []  # [(componente_id, cantidad), ...]
        self._equivalente_ids = []

        self.title("producto")
        self.geometry("1100x720")
        self.minsize(980, 640)
        self.configure(fg_color=("#E8E4DC", "#2B2B2B"))

        self.tab = ctk.CTkTabview(self)
        self.tab.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab.add("Información del producto")
        self.tab.add("Detalles adicionales")

        self._build_tab_info()
        self._build_tab_detalle()
        self._on_otro_impuesto_change()

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(0, 10))

        if not read_only:
            ctk.CTkButton(
                bar,
                text="Aceptar",
                width=130,
                height=34,
                fg_color="#2563EB",
                command=self._guardar,
            ).pack(side="left", padx=4)
        ctk.CTkButton(
            bar,
            text="Notas",
            width=100,
            height=34,
            fg_color="#475569",
            command=self._ir_a_notas,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bar,
            text="Cancelar",
            width=110,
            height=34,
            fg_color="#6B7280",
            command=self.destroy,
        ).pack(side="right", padx=4)

        self.transient(master.winfo_toplevel())
        self.grab_set()
        if self._duplicate_src is not None:
            self.product_id = self._duplicate_src
            self._is_new = False
            self._cargar_producto()
            self._convertir_a_duplicado()
        elif self._is_new:
            self._cargar_vacio()
        else:
            self._cargar_producto()

        if read_only:
            self._aplicar_solo_lectura()
        else:
            self.bind("<Control-s>", self._atajo_guardar)
            self.bind("<Control-S>", self._atajo_guardar)
        self.bind("<Escape>", self._atajo_cancelar)

        self.after(80, self._centrar, master)

    def _ir_a_notas(self):
        self.tab.set("Detalles adicionales")
        self.after(80, lambda: self.txt_notas.focus())

    def _atajo_guardar(self, _event=None):
        if not self.read_only:
            self._guardar()
        return "break"

    def _atajo_cancelar(self, _event=None):
        self.destroy()
        return "break"

    def _convertir_a_duplicado(self):
        """Tras cargar un producto existente, deja el formulario listo para guardar como nuevo."""
        self.product_id = None
        self._is_new = True
        self._duplicate_src = None
        self.var_codigo.set("")
        n = self.ent_nombre.get().strip()
        self.ent_nombre.delete(0, "end")
        self.ent_nombre.insert(0, f"{n} (copia)" if n else "Producto (copia)")
        self.ent_stock.delete(0, "end")
        self.ent_stock.insert(0, "0")
        self._proveedores_sec_ids = []
        self._componentes_rows = []
        self._equivalente_ids = []
        self._loaded_img_path = None
        self._apply_qty_mode()

    def _apply_qty_mode(self):
        if self._is_new:
            self.lbl_qty_caption.configure(text="Cantidad inicial:")
            self.ent_stock.configure(state="normal")
        else:
            self.lbl_qty_caption.configure(text="Cantidad total:")
            self.ent_stock.configure(state="disabled")

    def _centrar(self, master):
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            rx = master.winfo_rootx()
            ry = master.winfo_rooty()
            rw = master.winfo_width()
            rh = master.winfo_height()
            x = rx + (rw - w) // 2
            y = ry + (rh - h) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _build_tab_info(self):
        t = self.tab.tab("Información del producto")
        scroll = ctk.CTkScrollableFrame(t, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # —— Cabecera: código + nombre ——
        head = ctk.CTkFrame(scroll, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(head, text="Código:", width=70, anchor="w").pack(
            side="left", padx=(0, 6)
        )
        self.var_codigo = ctk.StringVar()
        self.ent_codigo = ctk.CTkEntry(
            head,
            textvariable=self.var_codigo,
            width=120,
            fg_color=("#FFF9C4", "#5C5B28"),
            border_color=("#F9E076", "#8B8650"),
        )
        self.ent_codigo.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(head, text="Nombre del producto:", anchor="w").pack(
            side="left", padx=(0, 8)
        )
        self.ent_nombre = ctk.CTkEntry(head, placeholder_text="Descripción del artículo")
        self.ent_nombre.pack(side="left", fill="x", expand=True, padx=(0, 4))

        tip = (
            "Precios de venta: sin ITBIS (la columna «Precio + Impto.» muestra el total de referencia)."
        )
        if not self.read_only:
            tip += " Atajos: Ctrl+S guardar · Esc cerrar."
        ctk.CTkLabel(
            scroll,
            text=tip,
            font=("Arial", 10),
            text_color=("gray35", "gray65"),
            wraplength=920,
            justify="left",
        ).pack(fill="x", pady=(2, 4))

        # —— Cuerpo tipo MONICA (4 zonas) ——
        body = ctk.CTkFrame(
            scroll,
            fg_color=("#F5F0E6", "#323232"),
            corner_radius=8,
            border_width=1,
            border_color=("#C4B8A8", "#444444"),
        )
        body.pack(fill="both", expand=True, pady=4)

        left = ctk.CTkFrame(body, fg_color="transparent")
        mid = ctk.CTkFrame(body, fg_color="transparent")
        right = ctk.CTkFrame(body, fg_color="transparent")
        far = ctk.CTkFrame(body, fg_color="transparent", width=150)
        left.pack(side="left", fill="y", padx=10, pady=10)
        mid.pack(side="left", fill="y", padx=8, pady=10)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=10)
        far.pack(side="right", fill="y", padx=10, pady=10)
        far.pack_propagate(False)

        # ----- IZQUIERDA: precios + tipo + cantidad + impuesto -----
        ctk.CTkLabel(
            left, text="Precio de venta en $", font=("Arial", 12, "bold")
        ).pack(anchor="w", pady=(0, 4))
        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="", width=28).grid(row=0, column=0)
        ctk.CTkLabel(hdr, text="Precio $", width=88, font=("Arial", 10, "bold")).grid(
            row=0, column=1, padx=2
        )
        ctk.CTkLabel(hdr, text="Utilidad %", width=88, font=("Arial", 10, "bold")).grid(
            row=0, column=2, padx=2
        )

        self.ent_precios = []
        self.lbl_util = []
        for i in range(4):
            r = ctk.CTkFrame(left, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=f"{i + 1}", width=28).pack(side="left", padx=2)
            e = ctk.CTkEntry(r, width=86, height=26, placeholder_text="0.00")
            e.pack(side="left", padx=2)
            self.ent_precios.append(e)
            lb = ctk.CTkLabel(r, text="0.0000", width=86, anchor="e")
            lb.pack(side="left", padx=4)
            self.lbl_util.append(lb)

        def _on_precio_cambiado(_e=None):
            self._actualizar_utilidad()
            self._refrescar_precios_mas_itbis()

        for e in self.ent_precios:
            e.bind("<KeyRelease>", lambda _e: _on_precio_cambiado())

        ctk.CTkLabel(left, text="El producto es:", font=("Arial", 11)).pack(
            anchor="w", pady=(12, 2)
        )
        tipo_row = ctk.CTkFrame(left, fg_color="transparent")
        tipo_row.pack(anchor="w")
        self.var_tipo = ctk.StringVar(value="Físico")
        for val in ("Físico", "Servicio", "Ocasional"):
            rb = ctk.CTkRadioButton(
                tipo_row, text=val, variable=self.var_tipo, value=val, width=90
            )
            rb.pack(side="left", padx=(0, 6))
            self._tipo_radios.append(rb)

        qrow = ctk.CTkFrame(left, fg_color="transparent")
        qrow.pack(fill="x", pady=(10, 4))
        self.lbl_qty_caption = ctk.CTkLabel(
            qrow, text="Cantidad inicial:", width=110, anchor="w"
        )
        self.lbl_qty_caption.pack(side="left")
        self.ent_stock = ctk.CTkEntry(qrow, width=80, height=26)
        self.ent_stock.pack(side="left", padx=4)

        frow = ctk.CTkFrame(left, fg_color="transparent")
        frow.pack(fill="x", pady=4)
        ctk.CTkLabel(frow, text="Facturar con precio:", width=120, anchor="w").pack(
            side="left"
        )
        self.combo_nivel_factura = ctk.CTkComboBox(
            frow, values=["1", "2", "3", "4"], width=56, height=26
        )
        self.combo_nivel_factura.set("1")
        self.combo_nivel_factura.pack(side="left", padx=4)

        ctk.CTkLabel(left, text="Impuesto", font=("Arial", 11, "bold")).pack(
            anchor="w", pady=(14, 4)
        )
        imp1 = ctk.CTkFrame(left, fg_color="transparent")
        imp1.pack(fill="x", pady=2)
        ctk.CTkLabel(imp1, text="Impuesto:", width=70, anchor="w").pack(side="left")
        self.combo_impuesto = ctk.CTkComboBox(
            imp1,
            values=["ITBIS", "Exento"],
            width=100,
            height=26,
            command=self._on_impuesto_change,
        )
        self.combo_impuesto.set("ITBIS")
        self.combo_impuesto.pack(side="left", padx=4)

        ch_tax = ctk.CTkFrame(left, fg_color="transparent")
        ch_tax.pack(fill="x", pady=4)
        ctk.CTkLabel(ch_tax, text="Aplicar en", width=70, anchor="w").pack(
            side="left", padx=(0, 8)
        )
        self.var_tax_ventas = ctk.BooleanVar(value=True)
        self.var_tax_compras = ctk.BooleanVar(value=False)
        self.chk_tax_ventas = ctk.CTkCheckBox(
            ch_tax,
            text="Ventas",
            variable=self.var_tax_ventas,
            command=self._refrescar_precios_mas_itbis,
        )
        self.chk_tax_ventas.pack(side="left", padx=8)
        self.chk_tax_compras = ctk.CTkCheckBox(
            ch_tax,
            text="Compras",
            variable=self.var_tax_compras,
        )
        self.chk_tax_compras.pack(side="left", padx=8)

        imp2 = ctk.CTkFrame(left, fg_color="transparent")
        imp2.pack(fill="x", pady=(8, 2))
        ctk.CTkLabel(imp2, text="Otro impto.:", width=70, anchor="w").pack(side="left")
        self.combo_otro_imp = ctk.CTkComboBox(
            imp2,
            values=OTROS_IMPUESTO_LABELS,
            width=110,
            height=26,
            command=self._on_otro_impuesto_change,
        )
        self.combo_otro_imp.set("Ninguno")
        self.combo_otro_imp.pack(side="left", padx=4)
        ch_ot = ctk.CTkFrame(left, fg_color="transparent")
        ch_ot.pack(fill="x", pady=2)
        ctk.CTkLabel(ch_ot, text="", width=70).pack(side="left")
        self.var_otro_ventas = ctk.BooleanVar(value=False)
        self.var_otro_compras = ctk.BooleanVar(value=False)
        self.chk_otro_ventas = ctk.CTkCheckBox(
            ch_ot,
            text="Ventas",
            variable=self.var_otro_ventas,
            command=self._refrescar_precios_mas_itbis,
        )
        self.chk_otro_ventas.pack(side="left", padx=8)
        self.chk_otro_compras = ctk.CTkCheckBox(
            ch_ot,
            text="Compras",
            variable=self.var_otro_compras,
        )
        self.chk_otro_compras.pack(side="left", padx=8)

        self.lbl_tax_ayuda = ctk.CTkLabel(
            left,
            text="",
            font=("Arial", 10),
            text_color=("gray30", "gray70"),
            wraplength=280,
            justify="left",
        )
        self.lbl_tax_ayuda.pack(anchor="w", pady=(4, 0))

        # ----- CENTRO: costo -----
        ctk.CTkLabel(
            mid, text="Costo del producto en $", font=("Arial", 12, "bold")
        ).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(mid, text="Por unidad:", anchor="w").pack(anchor="w")
        self.ent_costo = ctk.CTkEntry(mid, width=120, height=26, placeholder_text="0.00")
        self.ent_costo.pack(anchor="w", pady=(2, 8))
        self.ent_costo.bind("<KeyRelease>", lambda _e: _on_precio_cambiado())
        ctk.CTkLabel(mid, text="En US$:", anchor="w").pack(anchor="w")
        self.ent_costo_usd = ctk.CTkEntry(mid, width=120, height=26, placeholder_text="0.00")
        self.ent_costo_usd.pack(anchor="w", pady=(2, 8))
        self.var_activo = ctk.BooleanVar(value=True)
        self.chk_activo = ctk.CTkCheckBox(
            mid, text="Producto está activo", variable=self.var_activo
        )
        self.chk_activo.pack(anchor="w", pady=8)

        # ----- DERECHA: clasificación + unidades -----
        ctk.CTkLabel(
            right, text="Clasificación del producto", font=("Arial", 12, "bold")
        ).pack(anchor="w", pady=(0, 6))
        gf = ctk.CTkFrame(right, fg_color="transparent")
        gf.pack(fill="x")
        ctk.CTkLabel(gf, text="Categoría:", width=130, anchor="w").grid(
            row=0, column=0, sticky="w", pady=3
        )
        self.combo_cat = ctk.CTkComboBox(gf, values=[], width=200, height=26)
        self.combo_cat.grid(row=0, column=1, sticky="w", pady=3)
        ctk.CTkLabel(gf, text="Subcategoría:", width=130, anchor="w").grid(
            row=1, column=0, sticky="w", pady=3
        )
        self.combo_subcat = ctk.CTkComboBox(
            gf,
            values=["GEN — Genérico", "NAC — Nacional", "IMP — Importado", "OTR — Otro"],
            width=200,
            height=26,
        )
        self.combo_subcat.set("GEN — Genérico")
        self.combo_subcat.grid(row=1, column=1, sticky="w", pady=3)
        ctk.CTkLabel(gf, text="Asignado a bodega:", width=130, anchor="w").grid(
            row=2, column=0, sticky="w", pady=3
        )
        self.combo_bodega = ctk.CTkComboBox(
            gf,
            values=["PRI — Bodega principal", "SEC — Secundaria", "MOS — Mostrador"],
            width=200,
            height=26,
        )
        self.combo_bodega.set("PRI — Bodega principal")
        self.combo_bodega.grid(row=2, column=1, sticky="w", pady=3)
        ctk.CTkLabel(gf, text="Ubicación física:", width=130, anchor="w").grid(
            row=3, column=0, sticky="w", pady=3
        )
        self.ent_ubicacion = ctk.CTkEntry(gf, width=200, height=26)
        self.ent_ubicacion.grid(row=3, column=1, sticky="ew", pady=3)
        gf.columnconfigure(1, weight=1)

        ctk.CTkLabel(right, text="Código de barras:", anchor="w").pack(
            anchor="w", pady=(10, 2)
        )
        self.ent_barras = ctk.CTkEntry(right, height=26)
        self.ent_barras.pack(fill="x", pady=(0, 8))

        uvals = ["UN — Unidad", "DOC — Docena", "CAJ — Caja", "KG — Kg", "L — Litro", "BOT — Botella"]
        ctk.CTkLabel(right, text="Se vende por:", anchor="w").pack(anchor="w")
        uv = ctk.CTkFrame(right, fg_color="transparent")
        uv.pack(fill="x", pady=2)
        self.combo_unidad = ctk.CTkComboBox(uv, values=uvals, width=130, height=26)
        self.combo_unidad.set("UN — Unidad")
        self.combo_unidad.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(uv, text="y contiene").pack(side="left", padx=2)
        self.ent_cont_vende = ctk.CTkEntry(uv, width=70, height=26)
        self.ent_cont_vende.pack(side="left", padx=4)
        ctk.CTkLabel(uv, text="Unid.(s)").pack(side="left")

        ctk.CTkLabel(right, text="Se compra por:", anchor="w").pack(anchor="w", pady=(8, 0))
        uc = ctk.CTkFrame(right, fg_color="transparent")
        uc.pack(fill="x", pady=2)
        self.combo_unidad_compra = ctk.CTkComboBox(uc, values=uvals, width=130, height=26)
        self.combo_unidad_compra.set("UN — Unidad")
        self.combo_unidad_compra.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(uc, text="y contiene").pack(side="left", padx=2)
        self.ent_cont_compra = ctk.CTkEntry(uc, width=70, height=26)
        self.ent_cont_compra.pack(side="left", padx=4)
        ctk.CTkLabel(uc, text="Unid.(s)").pack(side="left")

        # ----- COLUMNA: Precio + Impto -----
        ctk.CTkLabel(
            far, text="Precio + Impto.", font=("Arial", 11, "bold")
        ).pack(anchor="w", pady=(0, 8))
        self.lbl_p_mas_impto = []
        for i in range(4):
            ctk.CTkLabel(far, text=f"Precio {i + 1} + Impto.", font=("Arial", 10)).pack(
                anchor="w", pady=(0, 2)
            )
            lb = ctk.CTkLabel(
                far,
                text="0.00",
                font=("Arial", 12, "bold"),
                text_color=("#1e3a5f", "#93C5FD"),
            )
            lb.pack(anchor="w", pady=(0, 10))
            self.lbl_p_mas_impto.append(lb)

        self._cargar_categorias_combo()

    def _on_impuesto_change(self, _choice=None):
        es_exento = self.combo_impuesto.get() == "Exento"
        if es_exento:
            self.var_tax_ventas.set(False)
            self.chk_tax_ventas.configure(state="disabled")
        else:
            self.chk_tax_ventas.configure(state="normal")
            if self._is_new or not hasattr(self, "_skip_tax_reset"):
                self.var_tax_ventas.set(True)
        self._refrescar_precios_mas_itbis()

    def _itbis_aplica_venta(self) -> bool:
        return self.combo_impuesto.get() == "ITBIS" and self.var_tax_ventas.get()

    def _tasa_otro_venta(self) -> float:
        if not self.var_otro_ventas.get():
            return 0.0
        lab = self.combo_otro_imp.get()
        return float(OTROS_IMPUESTO_TASA.get(lab, 0.0))

    def _on_otro_impuesto_change(self, _choice=None):
        if self.combo_otro_imp.get() == "Ninguno":
            self.var_otro_ventas.set(False)
            self.var_otro_compras.set(False)
            self.chk_otro_ventas.configure(state="disabled")
            self.chk_otro_compras.configure(state="disabled")
        else:
            self.chk_otro_ventas.configure(state="normal")
            self.chk_otro_compras.configure(state="normal")
        self._refrescar_precios_mas_itbis()

    def _refrescar_precios_mas_itbis(self):
        aplica = self._itbis_aplica_venta()
        t_otro = self._tasa_otro_venta()
        extra = ""
        if t_otro > 0:
            extra = f" + otro {int(t_otro * 100)}%"
        if self.combo_impuesto.get() == "Exento":
            self.lbl_tax_ayuda.configure(
                text="Exento: no se calculará ITBIS en factura para este producto."
                + (f" Otro impuesto en ventas:{extra}" if extra else "")
            )
        elif aplica:
            self.lbl_tax_ayuda.configure(
                text=(
                    f"ITBIS {int(ITBIS_TASA * 100)}% sobre precio sin impuesto{extra}."
                )
            )
        else:
            self.lbl_tax_ayuda.configure(
                text="ITBIS sin aplicar en ventas: columna derecha = precio base"
                + (f"; otro impto. ventas:{extra}" if extra else "")
                + "."
            )

        for i, e in enumerate(self.ent_precios):
            try:
                p = self._parse_float(e.get(), 0.0)
            except ValueError:
                self.lbl_p_mas_impto[i].configure(text="—")
                continue
            if p <= 0:
                self.lbl_p_mas_impto[i].configure(text="0.00")
                continue
            tasa_sum = 0.0
            if aplica:
                tasa_sum += ITBIS_TASA
            if t_otro > 0:
                tasa_sum += t_otro
            total = p * (1.0 + tasa_sum)
            self.lbl_p_mas_impto[i].configure(text=f"{total:,.2f}")

    def _build_tab_detalle(self):
        t = self.tab.tab("Detalles adicionales")
        scroll = ctk.CTkScrollableFrame(t, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        top = ctk.CTkFrame(scroll, fg_color="transparent")
        top.pack(fill="x", pady=(0, 8))

        left = ctk.CTkFrame(top, fg_color="transparent", width=200)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="Imagen", font=("Arial", 12, "bold")).pack(pady=(0, 4))
        self.image_selector = ModernImageSelector(
            left, self.image_manager, light_theme=True
        )
        self.image_selector.pack(fill="both", expand=True)

        mid = ctk.CTkFrame(top, fg_color="transparent")
        mid.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ctk.CTkLabel(mid, text="Descripción adicional del producto", anchor="w").pack(
            fill="x"
        )
        self.txt_desc = ctk.CTkTextbox(mid, height=140)
        self.txt_desc.pack(fill="x", pady=4)
        self.var_desc_en_factura = ctk.BooleanVar(value=False)
        self.chk_desc_factura = ctk.CTkCheckBox(
            mid,
            text="Adicionar este comentario en facturas y estimados",
            variable=self.var_desc_en_factura,
        )
        self.chk_desc_factura.pack(anchor="w", pady=(0, 8))

        right = ctk.CTkFrame(top, fg_color="transparent", width=260)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)
        ctk.CTkLabel(right, text="Código fabricante:", anchor="w").pack(fill="x")
        self.ent_codigo_fab = ctk.CTkEntry(right, height=26)
        self.ent_codigo_fab.pack(fill="x", pady=(2, 10))
        ctk.CTkLabel(right, text="Facturar sin existencia:", anchor="w").pack(fill="x")
        self.var_sin_existencia = ctk.StringVar(value="sí")
        fe = ctk.CTkFrame(right, fg_color="transparent")
        fe.pack(fill="x", pady=2)
        self._radio_sin_stock = []
        self._radio_sin_stock.append(
            ctk.CTkRadioButton(
                fe, text="Sí", variable=self.var_sin_existencia, value="sí", width=56
            )
        )
        self._radio_sin_stock[0].pack(side="left", padx=(0, 12))
        self._radio_sin_stock.append(
            ctk.CTkRadioButton(
                fe, text="No", variable=self.var_sin_existencia, value="no", width=56
            )
        )
        self._radio_sin_stock[1].pack(side="left")
        ctk.CTkLabel(right, text="Cantidad mínima (reorden):", anchor="w").pack(
            fill="x", pady=(10, 0)
        )
        self.ent_stock_min = ctk.CTkEntry(right, width=100, height=26)
        self.ent_stock_min.pack(anchor="w", pady=(2, 0))

        # —— Proveedores ——
        prov = ctk.CTkFrame(scroll, fg_color="transparent")
        prov.pack(fill="x", pady=12)
        ctk.CTkLabel(prov, text="Proveedor principal", font=("Arial", 12, "bold")).pack(
            anchor="w"
        )
        pr = ctk.CTkFrame(prov, fg_color="transparent")
        pr.pack(fill="x", pady=4)
        self.lbl_proveedor = ctk.CTkLabel(
            pr,
            text="(ninguno)",
            anchor="w",
            width=280,
            fg_color=("#F0EBE3", "#333333"),
            corner_radius=4,
        )
        self.lbl_proveedor.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            pr, text="Buscar", width=80, command=self._buscar_proveedor
        ).pack(side="left")

        pr2 = ctk.CTkFrame(prov, fg_color="transparent")
        pr2.pack(fill="x", pady=6)
        ctk.CTkLabel(
            pr2, text="Adicionar ó retirar otros proveedores:", anchor="w"
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            pr2, text="Asignar", width=90, command=self._asignar_proveedores_sec
        ).pack(side="left")

        # —— Componentes / equivalentes ——
        rowx = ctk.CTkFrame(scroll, fg_color="transparent")
        rowx.pack(fill="x", pady=8)
        ctk.CTkLabel(rowx, text="Producto puede tener componentes:", width=260, anchor="w").pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            rowx, text="Asignar comp.", width=110, command=self._asignar_componentes
        ).pack(side="left", padx=4)
        rowy = ctk.CTkFrame(scroll, fg_color="transparent")
        rowy.pack(fill="x", pady=4)
        ctk.CTkLabel(rowy, text="Producto puede tener equivalentes:", width=260, anchor="w").pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            rowy, text="Asignar equiv.", width=110, command=self._asignar_equivalentes
        ).pack(side="left", padx=4)

        # —— Contabilidad ——
        ctk.CTkLabel(
            scroll, text="Cuentas contables", font=("Arial", 12, "bold")
        ).pack(anchor="w", pady=(12, 4))
        self.ent_cta_ventas = self._fila_cuenta(
            scroll, "Al vender asignar a cuenta (Ventas / ingresos)"
        )
        self.ent_cta_gastos = self._fila_cuenta(
            scroll, "Al comprar asignar a la cuenta (Gastos)"
        )
        self.ent_cta_inventario = self._fila_cuenta(
            scroll, "Inventariar este producto en cta. (Activo)"
        )

        # —— Lotes / serie ——
        lot = ctk.CTkFrame(scroll, fg_color="transparent")
        lot.pack(fill="x", pady=12)
        self.var_lotes = ctk.BooleanVar(value=False)
        self.var_serie = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            lot,
            text="Producto con expiración (ej. medicinas)",
            variable=self.var_lotes,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkButton(
            lot, text="Asignar lotes", width=100, command=self._placeholder_lotes
        ).pack(side="left", padx=4)
        lot2 = ctk.CTkFrame(scroll, fg_color="transparent")
        lot2.pack(fill="x", pady=4)
        ctk.CTkCheckBox(
            lot2,
            text="Producto tiene nro. de serie (ej. celular)",
            variable=self.var_serie,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkButton(
            lot2, text="Asignar serial", width=100, command=self._placeholder_serie
        ).pack(side="left", padx=4)

        ctk.CTkLabel(scroll, text="Notas internas", anchor="w").pack(
            fill="x", pady=(16, 0)
        )
        self.txt_notas = ctk.CTkTextbox(scroll, height=120)
        self.txt_notas.pack(fill="x", pady=4)

    def _fila_cuenta(self, parent, texto):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", pady=3)
        ctk.CTkLabel(f, text=texto, width=320, anchor="w", wraplength=300).pack(
            side="left", padx=(0, 6)
        )
        e = ctk.CTkEntry(f, height=26)
        e.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            f, text="Buscar", width=72, command=lambda ent=e: self._buscar_cuenta(ent)
        ).pack(side="left")
        return e

    def _placeholder_lotes(self):
        messagebox.showinfo(
            "Lotes",
            "Marque el producto para control por lotes. "
            "El detalle de lotes se gestionará en el módulo de inventario / reportes.",
        )

    def _placeholder_serie(self):
        messagebox.showinfo(
            "Número de serie",
            "Marque el producto para requerir serie. "
            "La captura de seriales en venta se podrá enlazar en una siguiente fase.",
        )

    def _buscar_cuenta(self, entry_widget):
        d = ctk.CTkToplevel(self)
        d.title("Cuenta contable")
        d.geometry("400x160")
        d.transient(self)
        d.grab_set()
        ctk.CTkLabel(d, text="Código o nombre de cuenta:").pack(anchor="w", padx=12, pady=(12, 4))
        ent = ctk.CTkEntry(d, width=360)
        ent.pack(padx=12, pady=4)
        ent.focus()

        def ok():
            t = ent.get().strip()
            if t:
                entry_widget.delete(0, "end")
                entry_widget.insert(0, t)
            d.destroy()

        bf = ctk.CTkFrame(d, fg_color="transparent")
        bf.pack(pady=12)
        ctk.CTkButton(bf, text="Aceptar", command=ok).pack(side="left", padx=6)
        ctk.CTkButton(bf, text="Cancelar", command=d.destroy).pack(side="left", padx=6)

    def _buscar_proveedor(self):
        d = _ListaSeleccionDialog(
            self,
            "Proveedor principal",
            self.db.list_proveedores(),
            lambda r: f"{r[1]}  (doc: {r[2] or '—'})",
            db=self.db,
            allow_create=True,
        )
        self.wait_window(d)
        if d.seleccion is not None:
            self._proveedor_id = d.seleccion[0]
            self.lbl_proveedor.configure(text=d.seleccion[1])

    def _asignar_proveedores_sec(self):
        rows = self.db.list_proveedores()
        ids = _MultiSeleccionDialog(
            self,
            "Otros proveedores",
            rows,
            lambda r: f"{r[1]}",
            set(self._proveedores_sec_ids),
        )
        self.wait_window(ids)
        if ids.resultado is not None:
            self._proveedores_sec_ids = list(ids.resultado)

    def _asignar_componentes(self):
        pid = self.product_id if self.product_id else None
        d = _ComponentesDialog(self, self.db, pid, self._componentes_rows)
        self.wait_window(d)
        if d.guardado:
            self._componentes_rows = d.rows

    def _asignar_equivalentes(self):
        ex = self.product_id if self.product_id else None
        prods = self.db.list_productos_picker(exclude_id=ex)
        ids = _MultiSeleccionDialog(
            self,
            "Productos equivalentes",
            prods,
            lambda r: f"{r[1] or '—'} — {r[2]}",
            set(self._equivalente_ids),
            id_index=0,
        )
        self.wait_window(ids)
        if ids.resultado is not None:
            self._equivalente_ids = list(ids.resultado)

    def _cargar_categorias_combo(self):
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT nombre FROM categorias ORDER BY nombre")
        names = [r[0] for r in cur.fetchall()]
        conn.close()
        if names:
            self.combo_cat.configure(values=names)
            self.combo_cat.set(names[0])
        else:
            self.combo_cat.configure(values=["—"])
            self.combo_cat.set("—")

    def _parse_float(self, s, default=0.0):
        s = (s or "").strip().replace(",", "")
        if not s:
            return default
        return float(s)

    def _actualizar_utilidad(self):
        try:
            costo = self._parse_float(self.ent_costo.get(), 0.0)
        except ValueError:
            return
        for i, e in enumerate(self.ent_precios):
            try:
                p = self._parse_float(e.get(), 0.0)
            except ValueError:
                self.lbl_util[i].configure(text="—")
                continue
            if costo > 0 and p > 0:
                u = (p - costo) / costo * 100.0
                self.lbl_util[i].configure(text=f"{u:.4f}")
            else:
                self.lbl_util[i].configure(text="0.0000")

    @staticmethod
    def _codigo_combo_medida(texto: str) -> str:
        if "—" in texto:
            return texto.split("—")[0].strip()
        return texto.strip()[:3] if texto else "UN"

    def _cargar_vacio(self):
        self._loaded_img_path = None
        self._skip_tax_reset = True
        self._proveedor_id = None
        self.lbl_proveedor.configure(text="(ninguno)")
        self._proveedores_sec_ids = []
        self._componentes_rows = []
        self._equivalente_ids = []
        self.var_codigo.set("")
        self.ent_nombre.delete(0, "end")
        self.ent_costo.delete(0, "end")
        self.ent_costo_usd.delete(0, "end")
        for e in self.ent_precios:
            e.delete(0, "end")
        self.ent_stock.delete(0, "end")
        self.ent_stock.insert(0, "0")
        self.ent_stock_min.delete(0, "end")
        self.ent_stock_min.insert(0, "5")
        self.ent_barras.delete(0, "end")
        self.ent_ubicacion.delete(0, "end")
        self.ent_cont_vende.delete(0, "end")
        self.ent_cont_vende.insert(0, "1.00")
        self.ent_cont_compra.delete(0, "end")
        self.ent_cont_compra.insert(0, "1.00")
        self.txt_desc.delete("1.0", "end")
        self.txt_notas.delete("1.0", "end")
        self.image_selector.clear_image()
        self.var_tipo.set("Físico")
        self.var_activo.set(True)
        self.combo_unidad.set("UN — Unidad")
        self.combo_unidad_compra.set("UN — Unidad")
        self.combo_subcat.set("GEN — Genérico")
        self.combo_bodega.set("PRI — Bodega principal")
        self.combo_nivel_factura.set("1")
        self.combo_impuesto.set("ITBIS")
        self.var_tax_ventas.set(True)
        self.var_tax_compras.set(False)
        self.chk_tax_ventas.configure(state="normal")
        self.var_desc_en_factura.set(False)
        self.ent_codigo_fab.delete(0, "end")
        self.var_sin_existencia.set("sí")
        self.var_lotes.set(False)
        self.var_serie.set(False)
        self.ent_cta_ventas.delete(0, "end")
        self.ent_cta_gastos.delete(0, "end")
        self.ent_cta_inventario.delete(0, "end")
        self.combo_otro_imp.set("Ninguno")
        self.var_otro_ventas.set(False)
        self.var_otro_compras.set(False)
        del self._skip_tax_reset
        self._on_otro_impuesto_change()
        self._apply_qty_mode()
        self._actualizar_utilidad()
        self._refrescar_precios_mas_itbis()

    def _cargar_producto(self):
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(productos)")
        have = {row[1] for row in cur.fetchall()}
        cur.execute(
            """
            SELECT p.id, p.nombre, p.descripcion, p.precio, p.precio_base, p.precio_minimo,
                   p.precio_2, p.precio_3, p.precio_4,
                   p.stock, p.categoria_id, p.stock_minimo, p.codigo_barras,
                   p.imagen_path, p.activo, c.nombre,
                   IFNULL(p.codigo_producto,''), IFNULL(p.tipo_producto,'Físico'),
                   IFNULL(p.unidad_medida,'Unidad'), IFNULL(p.ubicacion,''),
                   IFNULL(p.notas_internas,''), IFNULL(p.aplica_itbis, 1),
                   IFNULL(p.costo_usd,0), IFNULL(p.subcategoria_codigo,'GEN'),
                   IFNULL(p.bodega_codigo,'PRI'), IFNULL(p.facturar_nivel_precio,1),
                   IFNULL(p.aplica_itbis_compras,0),
                   IFNULL(p.contenido_unidad_venta,1), IFNULL(p.contenido_unidad_compra,1),
                   IFNULL(p.unidad_compra,'Unidad'),
                   IFNULL(p.descripcion_en_factura,0), IFNULL(p.codigo_fabricante,''),
                   IFNULL(p.facturar_sin_stock,1), p.proveedor_id,
                   IFNULL(pr.nombre,''), IFNULL(p.cuenta_ventas,''), IFNULL(p.cuenta_gastos,''),
                   IFNULL(p.cuenta_inventario,''),
                   IFNULL(p.tiene_control_lotes,0), IFNULL(p.tiene_numero_serie,0),
                   IFNULL(p.otro_impuesto,''), IFNULL(p.otro_impuesto_ventas,0),
                   IFNULL(p.otro_impuesto_compras,0)
            FROM productos p
            LEFT JOIN categorias c ON p.categoria_id = c.id
            LEFT JOIN proveedores pr ON p.proveedor_id = pr.id
            WHERE p.id = ?
            """,
            (self.product_id,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            messagebox.showerror("Error", "Producto no encontrado.")
            self.destroy()
            return

        (
            _id,
            nombre,
            descripcion,
            precio,
            pbase,
            pmin,
            p2,
            p3,
            p4,
            stock,
            _cat_id,
            stock_min,
            barras,
            img_path,
            activo,
            cat_nombre,
            codigo,
            tipo,
            unidad,
            ubicacion,
            notas,
            aplica_itbis,
            costo_usd,
            subcat,
            bodega,
            nivel_f,
            itbis_compra,
            cont_v,
            cont_c,
            unidad_c,
            desc_fact,
            cod_fab,
            sin_stock,
            prov_id,
            prov_nom,
            cta_v,
            cta_g,
            cta_inv,
            ctl_lotes,
            ctl_serie,
            otro_imp,
            otro_v,
            otro_c,
        ) = row

        self.var_codigo.set(codigo or "")
        self.ent_nombre.delete(0, "end")
        self.ent_nombre.insert(0, nombre or "")
        self.ent_costo.delete(0, "end")
        self.ent_costo.insert(0, f"{float(pbase or precio or 0):.2f}")
        self.ent_costo_usd.delete(0, "end")
        if costo_usd and float(costo_usd) > 0:
            self.ent_costo_usd.insert(0, f"{float(costo_usd):.2f}")

        prices = [precio, p2, p3, p4]
        for i, e in enumerate(self.ent_precios):
            e.delete(0, "end")
            v = prices[i] if i < len(prices) else None
            if v is not None and float(v) > 0:
                e.insert(0, f"{float(v):.2f}")

        self.ent_stock.delete(0, "end")
        self.ent_stock.insert(0, str(int(stock or 0)))
        self.ent_stock_min.delete(0, "end")
        self.ent_stock_min.insert(0, str(int(stock_min or 5)))
        self.ent_barras.delete(0, "end")
        if barras:
            self.ent_barras.insert(0, barras)
        self.ent_ubicacion.delete(0, "end")
        if ubicacion:
            self.ent_ubicacion.insert(0, ubicacion)
        self.txt_desc.delete("1.0", "end")
        if descripcion:
            self.txt_desc.insert("1.0", descripcion)
        self.txt_notas.delete("1.0", "end")
        if notas:
            self.txt_notas.insert("1.0", notas)
        if cat_nombre and cat_nombre in self.combo_cat.cget("values"):
            self.combo_cat.set(cat_nombre)
        self.var_tipo.set(tipo or "Físico")
        self.var_activo.set(bool(activo))

        uvals = list(self.combo_unidad.cget("values"))

        def match_u(u, combo):
            if not u:
                combo.set("UN — Unidad")
                return
            u = str(u).strip().lower()
            for opt in uvals:
                part = opt.split("—", 1)[-1].strip().lower()
                code = opt.split("—", 1)[0].strip().lower()
                if u == part or u == code or part.startswith(u) or u.startswith(code):
                    combo.set(opt)
                    return
            combo.set("UN — Unidad")

        match_u(unidad, self.combo_unidad)
        self.ent_cont_vende.delete(0, "end")
        self.ent_cont_vende.insert(0, f"{float(cont_v or 1):.2f}")
        self.ent_cont_compra.delete(0, "end")
        self.ent_cont_compra.insert(0, f"{float(cont_c or 1):.2f}")
        match_u(unidad_c, self.combo_unidad_compra)

        sc = (subcat or "GEN").upper()[:3]
        for opt in self.combo_subcat.cget("values"):
            if opt.startswith(sc):
                self.combo_subcat.set(opt)
                break
        bc = (bodega or "PRI").upper()[:3]
        for opt in self.combo_bodega.cget("values"):
            if opt.startswith(bc):
                self.combo_bodega.set(opt)
                break

        self.combo_nivel_factura.set(str(int(nivel_f or 1)))
        ai = int(aplica_itbis or 1)
        self._skip_tax_reset = True
        if ai:
            self.combo_impuesto.set("ITBIS")
            self.var_tax_ventas.set(True)
        else:
            self.combo_impuesto.set("Exento")
            self.var_tax_ventas.set(False)
        self.var_tax_compras.set(bool(int(itbis_compra or 0)))
        del self._skip_tax_reset
        self._on_impuesto_change()

        self.var_desc_en_factura.set(bool(int(desc_fact or 0)))
        self.ent_codigo_fab.delete(0, "end")
        if cod_fab:
            self.ent_codigo_fab.insert(0, cod_fab)
        self.var_sin_existencia.set("sí" if int(sin_stock or 1) else "no")
        if prov_id:
            self._proveedor_id = int(prov_id)
            self.lbl_proveedor.configure(text=prov_nom or "(proveedor)")
        else:
            self._proveedor_id = None
            self.lbl_proveedor.configure(text="(ninguno)")
        self.ent_cta_ventas.delete(0, "end")
        if cta_v:
            self.ent_cta_ventas.insert(0, cta_v)
        self.ent_cta_gastos.delete(0, "end")
        if cta_g:
            self.ent_cta_gastos.insert(0, cta_g)
        self.ent_cta_inventario.delete(0, "end")
        if cta_inv:
            self.ent_cta_inventario.insert(0, cta_inv)
        self.var_lotes.set(bool(int(ctl_lotes or 0)))
        self.var_serie.set(bool(int(ctl_serie or 0)))

        oi = (otro_imp or "").strip()
        if oi in OTROS_IMPUESTO_LABELS:
            self.combo_otro_imp.set(oi)
        else:
            self.combo_otro_imp.set("Ninguno")
        self.var_otro_ventas.set(bool(int(otro_v or 0)))
        self.var_otro_compras.set(bool(int(otro_c or 0)))
        self._on_otro_impuesto_change()

        self._proveedores_sec_ids = self.db.get_proveedores_secundarios_producto(
            self.product_id
        )
        self._componentes_rows = [
            (int(r[0]), float(r[1]))
            for r in self.db.get_componentes_producto(self.product_id)
        ]
        self._equivalente_ids = [
            int(r[0]) for r in self.db.get_equivalentes_producto(self.product_id)
        ]

        self.image_selector.clear_image()
        self._loaded_img_path = None
        if "imagen_path" in have and img_path and os.path.isfile(img_path):
            self.image_selector.load_existing_image(img_path)
            self._loaded_img_path = img_path

        self._apply_qty_mode()
        self._actualizar_utilidad()
        self._refrescar_precios_mas_itbis()

    def _aplicar_solo_lectura(self):
        for w in (
            self.ent_codigo,
            self.ent_nombre,
            self.ent_costo,
            self.ent_costo_usd,
            *self.ent_precios,
            self.ent_stock,
            self.ent_stock_min,
            self.ent_barras,
            self.ent_ubicacion,
            self.ent_cont_vende,
            self.ent_cont_compra,
            self.combo_cat,
            self.combo_subcat,
            self.combo_bodega,
            self.combo_unidad,
            self.combo_unidad_compra,
            self.combo_nivel_factura,
            self.combo_impuesto,
            self.combo_otro_imp,
            self.chk_activo,
            self.chk_tax_ventas,
            self.chk_tax_compras,
            self.chk_otro_ventas,
            self.chk_otro_compras,
            self.txt_desc,
            self.txt_notas,
            self.ent_codigo_fab,
            self.ent_cta_ventas,
            self.ent_cta_gastos,
            self.ent_cta_inventario,
            self.chk_desc_factura,
        ):
            w.configure(state="disabled")
        for rb in self._tipo_radios:
            rb.configure(state="disabled")
        for rb in getattr(self, "_radio_sin_stock", []):
            rb.configure(state="disabled")
        for attr in ("buscar_top_btn", "foto_top_btn", "select_btn", "clear_btn"):
            b = getattr(self.image_selector, attr, None)
            if b is not None:
                try:
                    b.configure(state="disabled")
                except Exception:
                    pass

    def _guardar(self):
        nombre = self.ent_nombre.get().strip()
        if not nombre:
            messagebox.showwarning("Validación", "El nombre del producto es obligatorio.")
            return
        try:
            costo = self._parse_float(self.ent_costo.get(), 0.0)
            costo_usd = self._parse_float(self.ent_costo_usd.get(), 0.0)
            pv = self._parse_float(self.ent_precios[0].get(), 0.0)
            p2 = self._parse_float(self.ent_precios[1].get(), 0.0)
            p3 = self._parse_float(self.ent_precios[2].get(), 0.0)
            p4 = self._parse_float(self.ent_precios[3].get(), 0.0)
            stock = int(float(self.ent_stock.get().strip() or "0"))
            smin = int(float(self.ent_stock_min.get().strip() or "0"))
            cont_v = self._parse_float(self.ent_cont_vende.get(), 1.0)
            cont_c = self._parse_float(self.ent_cont_compra.get(), 1.0)
            nivel_f = int(self.combo_nivel_factura.get() or "1")
            if nivel_f < 1 or nivel_f > 4:
                nivel_f = 1
        except ValueError:
            messagebox.showerror("Error", "Revise números en precios, stock y unidades.")
            return

        if pv <= 0:
            messagebox.showwarning("Validación", "Indique al menos el precio de venta nivel 1.")
            return

        precio_base = costo if costo > 0 else pv
        cands = [pv]
        for x in (p2, p3, p4):
            if x and x > 0:
                cands.append(x)
        precio_min = min(cands)

        aplica_itbis = 1 if self._itbis_aplica_venta() else 0
        aplica_itbis_compras = (
            1
            if self.combo_impuesto.get() == "ITBIS" and self.var_tax_compras.get()
            else 0
        )

        subcat_c = self._codigo_combo_medida(self.combo_subcat.get())[:3]
        bodega_c = self._codigo_combo_medida(self.combo_bodega.get())[:3]
        unidad_v = self.combo_unidad.get()
        unidad_c_nom = self.combo_unidad_compra.get()
        # Guardar medida legible (texto después de —) o completo
        def medida_larga(txt):
            if "—" in txt:
                return txt.split("—", 1)[1].strip()
            return txt.strip()

        um_venta = medida_larga(unidad_v)
        um_compra = medida_larga(unidad_c_nom)

        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM categorias WHERE nombre = ?", (self.combo_cat.get(),)
        )
        cr = cur.fetchone()
        if not cr:
            messagebox.showerror("Error", "Categoría no válida.")
            conn.close()
            return
        cat_id = cr[0]

        codigo = self.var_codigo.get().strip()
        barras = self.ent_barras.get().strip() or None
        desc = self.txt_desc.get("1.0", "end").strip()
        notas = self.txt_notas.get("1.0", "end").strip()
        ubic = self.ent_ubicacion.get().strip() or None
        tipo = self.var_tipo.get()
        activo = 1 if self.var_activo.get() else 0

        img_path = self.image_selector.get_image_path()
        must_copy_img = bool(
            img_path and (self._is_new or img_path != self._loaded_img_path)
        )

        desc_en_factura = 1 if self.var_desc_en_factura.get() else 0
        cod_fab = self.ent_codigo_fab.get().strip() or None
        facturar_sin_stock = 1 if self.var_sin_existencia.get() == "sí" else 0
        otro_lab = self.combo_otro_imp.get()
        if otro_lab == "Ninguno":
            otro_st = ""
            otro_iv = 0
            otro_ic = 0
        else:
            otro_st = otro_lab
            otro_iv = 1 if self.var_otro_ventas.get() else 0
            otro_ic = 1 if self.var_otro_compras.get() else 0

        cta_v = self.ent_cta_ventas.get().strip() or None
        cta_g = self.ent_cta_gastos.get().strip() or None
        cta_i = self.ent_cta_inventario.get().strip() or None
        ctl_l = 1 if self.var_lotes.get() else 0
        ctl_s = 1 if self.var_serie.get() else 0

        cols_ins = """
            nombre, descripcion, precio, precio_base, precio_minimo,
            precio_2, precio_3, precio_4,
            stock, categoria_id, stock_minimo, codigo_barras,
            codigo_producto, tipo_producto, unidad_medida, ubicacion,
            notas_internas, activo, aplica_itbis,
            costo_usd, subcategoria_codigo, bodega_codigo, facturar_nivel_precio,
            aplica_itbis_compras, contenido_unidad_venta, contenido_unidad_compra,
            unidad_compra,
            proveedor_id, descripcion_en_factura, codigo_fabricante, facturar_sin_stock,
            cuenta_ventas, cuenta_gastos, cuenta_inventario,
            tiene_control_lotes, tiene_numero_serie,
            otro_impuesto, otro_impuesto_ventas, otro_impuesto_compras
        """
        vals_ins = (
            nombre,
            desc,
            pv,
            precio_base,
            precio_min,
            p2 or None,
            p3 or None,
            p4 or None,
            stock,
            cat_id,
            smin,
            barras,
            codigo or None,
            tipo,
            um_venta,
            ubic,
            notas,
            activo,
            aplica_itbis,
            costo_usd or None,
            subcat_c,
            bodega_c,
            nivel_f,
            aplica_itbis_compras,
            cont_v,
            cont_c,
            um_compra,
            self._proveedor_id,
            desc_en_factura,
            cod_fab,
            facturar_sin_stock,
            cta_v,
            cta_g,
            cta_i,
            ctl_l,
            ctl_s,
            otro_st,
            otro_iv,
            otro_ic,
        )

        try:
            new_id = None
            if self._is_new:
                qm = ",".join(["?"] * 39)
                cur.execute(
                    f"INSERT INTO productos ({cols_ins}) VALUES ({qm})",
                    vals_ins,
                )
                new_id = cur.lastrowid
                if not codigo:
                    auto = f"P-{new_id:05d}"
                    cur.execute(
                        "UPDATE productos SET codigo_producto = ? WHERE id = ?",
                        (auto, new_id),
                    )
                if must_copy_img:
                    final = self.image_manager.copy_image_to_app(img_path, new_id)
                    if final:
                        cur.execute(
                            "UPDATE productos SET imagen_path = ? WHERE id = ?",
                            (final, new_id),
                        )
            else:
                cur.execute(
                    f"""
                    UPDATE productos SET
                        nombre=?, descripcion=?, precio=?, precio_base=?, precio_minimo=?,
                        precio_2=?, precio_3=?, precio_4=?,
                        stock=?, categoria_id=?, stock_minimo=?, codigo_barras=?,
                        codigo_producto=?, tipo_producto=?, unidad_medida=?, ubicacion=?,
                        notas_internas=?, activo=?, aplica_itbis=?,
                        costo_usd=?, subcategoria_codigo=?, bodega_codigo=?,
                        facturar_nivel_precio=?, aplica_itbis_compras=?,
                        contenido_unidad_venta=?, contenido_unidad_compra=?,
                        unidad_compra=?,
                        proveedor_id=?, descripcion_en_factura=?, codigo_fabricante=?,
                        facturar_sin_stock=?,
                        cuenta_ventas=?, cuenta_gastos=?, cuenta_inventario=?,
                        tiene_control_lotes=?, tiene_numero_serie=?,
                        otro_impuesto=?, otro_impuesto_ventas=?, otro_impuesto_compras=?
                    WHERE id=?
                    """,
                    vals_ins + (self.product_id,),
                )
                if must_copy_img:
                    final = self.image_manager.copy_image_to_app(
                        img_path, self.product_id
                    )
                    if final:
                        cur.execute(
                            "UPDATE productos SET imagen_path = ? WHERE id = ?",
                            (final, self.product_id),
                        )

            conn.commit()
            conn.close()

            final_id = new_id if new_id is not None else self.product_id
            sec_ids = [
                x
                for x in self._proveedores_sec_ids
                if self._proveedor_id is None or int(x) != int(self._proveedor_id)
            ]
            try:
                self.db.set_proveedores_secundarios_producto(final_id, sec_ids)
                self.db.set_componentes_producto(final_id, self._componentes_rows)
                self.db.set_equivalentes_producto(final_id, self._equivalente_ids)
            except Exception as ex:
                messagebox.showwarning(
                    "Relaciones",
                    f"Producto guardado; al guardar proveedores/componentes/equiv.: {ex}",
                )

            messagebox.showinfo("Éxito", "Producto guardado correctamente.")
            if self.on_saved:
                self.on_saved()
            self.destroy()
        except Exception as e:
            conn.close()
            messagebox.showerror("Error", f"No se pudo guardar: {e}")
