"""
Promociones, compras, métodos de pago, resumen de ventas, NCF, etiquetas y transferencias.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import customtkinter as ctk
from tkinter import messagebox, ttk

from database import Database


class PromocionesFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="#1e293b")
        self.db = Database()
        ctk.CTkLabel(self, text="Promociones", font=("Arial", 16, "bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkLabel(
            self,
            text="Se aplican solas en el mostrador cuando el producto está en la promoción y no hay descuento manual.",
            font=("Arial", 11),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=12)

        form = ctk.CTkFrame(self, fg_color="#0f172a")
        form.pack(fill="x", padx=10, pady=8)
        self.ent_nombre = ctk.CTkEntry(form, width=180, placeholder_text="Nombre")
        self.ent_nombre.grid(row=0, column=0, padx=6, pady=8)
        self.combo_tipo = ctk.CTkComboBox(
            form, values=["porcentaje", "fijo", "2x1", "3x2"], width=130
        )
        self.combo_tipo.set("porcentaje")
        self.combo_tipo.grid(row=0, column=1, padx=6)
        self.ent_valor = ctk.CTkEntry(form, width=80, placeholder_text="Valor")
        self.ent_valor.grid(row=0, column=2, padx=6)
        self.ent_prod = ctk.CTkEntry(form, width=120, placeholder_text="Id producto")
        self.ent_prod.grid(row=0, column=3, padx=6)
        ctk.CTkButton(form, text="Crear", width=90, command=self._crear).grid(
            row=0, column=4, padx=6
        )

        self.tree = ttk.Treeview(
            self,
            columns=("nombre", "tipo", "valor", "items", "estado"),
            show="headings",
            height=12,
        )
        for c, t, w in (
            ("nombre", "Nombre", 180),
            ("tipo", "Tipo", 110),
            ("valor", "Valor", 80),
            ("items", "Ítems", 70),
            ("estado", "Estado", 90),
        ):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=10, pady=4)
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(bf, text="Activar / desactivar", command=self._toggle).pack(side="left")
        self._cargar()

    def _cargar(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for pid, nombre, tipo, valor, activo, n in self.db.listar_promociones():
            self.tree.insert(
                "",
                "end",
                iid=str(pid),
                values=(nombre, tipo, valor, n, "Activa" if activo else "Inactiva"),
            )

    def _crear(self):
        nombre = (self.ent_nombre.get() or "").strip()
        if len(nombre) < 2:
            messagebox.showwarning("Promoción", "Indique un nombre.", parent=self)
            return
        tipo = self.combo_tipo.get()
        try:
            valor = float((self.ent_valor.get() or "0").replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror("Promoción", "El valor debe ser numérico.", parent=self)
            return
        raw = (self.ent_prod.get() or "").strip()
        producto_id = int(raw) if raw.isdigit() else None
        if producto_id is None:
            messagebox.showwarning(
                "Promoción", "Indique el id del producto (se ve en inventario).", parent=self
            )
            return
        self.db.guardar_promocion(nombre, tipo, valor, producto_id=producto_id)
        self.ent_nombre.delete(0, "end")
        self._cargar()

    def _toggle(self):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        estado = self.tree.item(sel[0], "values")[4]
        self.db.set_promocion_activa(pid, estado != "Activa")
        self._cargar()


class ComprasFrame(ctk.CTkFrame):
    def __init__(self, parent, current_user=None):
        super().__init__(parent, fg_color="#1e293b")
        self.db = Database()
        self.current_user = current_user
        ctk.CTkLabel(self, text="Compras a proveedor", font=("Arial", 16, "bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkLabel(
            self,
            text="Cada compra suma existencia en la bodega elegida y queda en el kardex.",
            font=("Arial", 11),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=12)

        form = ctk.CTkFrame(self, fg_color="#0f172a")
        form.pack(fill="x", padx=10, pady=8)
        provs = self.db.list_proveedores()
        self._prov_ids = {f"{n}": i for i, n, *_ in provs}
        nombres = list(self._prov_ids) or ["(sin proveedores)"]
        self.combo_prov = ctk.CTkComboBox(form, values=nombres, width=220)
        self.combo_prov.set(nombres[0])
        self.combo_prov.grid(row=0, column=0, padx=6, pady=8)
        self.ent_nuevo_prov = ctk.CTkEntry(form, width=160, placeholder_text="Nuevo proveedor")
        self.ent_nuevo_prov.grid(row=0, column=1, padx=6)
        ctk.CTkButton(form, text="Agregar", width=80, command=self._alta_prov).grid(
            row=0, column=2, padx=4
        )
        self.ent_prod = ctk.CTkEntry(form, width=100, placeholder_text="Id producto")
        self.ent_prod.grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.ent_cant = ctk.CTkEntry(form, width=80, placeholder_text="Cantidad")
        self.ent_cant.grid(row=1, column=1, padx=6, sticky="w")
        self.ent_costo = ctk.CTkEntry(form, width=90, placeholder_text="Costo")
        self.ent_costo.grid(row=1, column=2, padx=6, sticky="w")
        bods = self.db.list_bodegas_codigos()
        self.combo_bod = ctk.CTkComboBox(form, values=bods, width=140)
        self.combo_bod.set(bods[0])
        self.combo_bod.grid(row=1, column=3, padx=6)
        ctk.CTkButton(form, text="Registrar compra", fg_color="#15803d", command=self._comprar).grid(
            row=2, column=0, columnspan=2, padx=6, pady=8, sticky="w"
        )
        self.lbl = ctk.CTkLabel(self, text="", font=("Arial", 12))
        self.lbl.pack(anchor="w", padx=12)

    def _alta_prov(self):
        nom = (self.ent_nuevo_prov.get() or "").strip()
        if len(nom) < 2:
            messagebox.showwarning("Proveedor", "Indique el nombre.", parent=self)
            return
        pid = self.db.crear_proveedor(nom)
        self._prov_ids[nom] = pid
        vals = list(self._prov_ids)
        self.combo_prov.configure(values=vals)
        self.combo_prov.set(nom)
        self.ent_nuevo_prov.delete(0, "end")

    def _comprar(self):
        raw = (self.ent_prod.get() or "").strip()
        if not raw.isdigit():
            messagebox.showwarning("Compra", "Indique el id del producto.", parent=self)
            return
        try:
            cant = float((self.ent_cant.get() or "").replace(",", "."))
            costo = float((self.ent_costo.get() or "0").replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror("Compra", "Cantidad y costo deben ser números.", parent=self)
            return
        prov_id = self._prov_ids.get(self.combo_prov.get())
        try:
            cid = self.db.registrar_compra(
                prov_id, int(raw), cant, costo, self.combo_bod.get(), self.current_user
            )
        except Exception as e:
            messagebox.showerror("Compra", str(e), parent=self)
            return
        self.lbl.configure(text=f"Compra {cid} registrada. Entraron {cant:g} unidades.")
        self.ent_cant.delete(0, "end")


class MetodosPagoFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="#1e293b")
        self.db = Database()
        ctk.CTkLabel(self, text="Métodos de pago", font=("Arial", 16, "bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        ctk.CTkLabel(
            self,
            text="Si marca «Entra a caja», el monto suma al efectivo esperado del turno.",
            font=("Arial", 11),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=12)
        form = ctk.CTkFrame(self, fg_color="#0f172a")
        form.pack(fill="x", padx=10, pady=8)
        self.ent_cod = ctk.CTkEntry(form, width=120, placeholder_text="código")
        self.ent_cod.grid(row=0, column=0, padx=6, pady=8)
        self.ent_nom = ctk.CTkEntry(form, width=160, placeholder_text="Nombre")
        self.ent_nom.grid(row=0, column=1, padx=6)
        self.var_caja = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Entra a caja", variable=self.var_caja).grid(
            row=0, column=2, padx=6
        )
        ctk.CTkButton(form, text="Guardar", command=self._guardar).grid(row=0, column=3, padx=6)
        self.tree = ttk.Treeview(
            self, columns=("codigo", "nombre", "caja", "estado"), show="headings", height=10
        )
        for c, t, w in (
            ("codigo", "Código", 120),
            ("nombre", "Nombre", 180),
            ("caja", "En caja", 80),
            ("estado", "Estado", 90),
        ):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=10, pady=4)
        ctk.CTkButton(self, text="Activar / desactivar", command=self._toggle).pack(
            anchor="w", padx=10, pady=8
        )
        self._cargar()

    def _cargar(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for cod, nom, caja, activo in self.db.listar_metodos_pago(solo_activos=False):
            self.tree.insert(
                "",
                "end",
                iid=cod,
                values=(cod, nom, "Sí" if caja else "No", "Activo" if activo else "Inactivo"),
            )

    def _guardar(self):
        cod = (self.ent_cod.get() or "").strip().lower().replace(" ", "_")
        nom = (self.ent_nom.get() or "").strip()
        if len(cod) < 2 or len(nom) < 2:
            messagebox.showwarning("Método", "Código y nombre son obligatorios.", parent=self)
            return
        self.db.guardar_metodo_pago(cod, nom, self.var_caja.get(), True)
        self._cargar()

    def _toggle(self):
        sel = self.tree.selection()
        if not sel:
            return
        cod, nom, caja, estado = self.tree.item(sel[0], "values")
        self.db.guardar_metodo_pago(cod, nom, caja == "Sí", estado != "Activo")
        self._cargar()


class ResumenPosFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="#1e293b")
        self.db = Database()
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=8)
        ctk.CTkLabel(top, text="Resumen del mostrador", font=("Arial", 15, "bold")).pack(
            side="left"
        )
        self.combo = ctk.CTkComboBox(top, values=["7", "30", "90"], width=80, command=lambda _v: self._cargar())
        self.combo.set("30")
        self.combo.pack(side="right")
        ctk.CTkLabel(top, text="Días").pack(side="right", padx=6)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=4)
        self.tabs.add("Más vendidos")
        self.tabs.add("Por cajero")
        self.tabs.add("Por forma de pago")
        self.trees = {}
        specs = {
            "Más vendidos": (("producto", "Producto", 280), ("cant", "Cantidad", 90), ("total", "Total", 100)),
            "Por cajero": (("cajero", "Cajero", 180), ("docs", "Facturas", 90), ("total", "Total", 120)),
            "Por forma de pago": (("metodo", "Método", 180), ("n", "Pagos", 80), ("total", "Total", 120)),
        }
        for name, cols in specs.items():
            tab = self.tabs.tab(name)
            tree = ttk.Treeview(tab, columns=[c[0] for c in cols], show="headings", height=14)
            for c, t, w in cols:
                tree.heading(c, text=t)
                tree.column(c, width=w)
            tree.pack(fill="both", expand=True, padx=4, pady=4)
            self.trees[name] = tree
        self._cargar()

    def _dias(self) -> int:
        try:
            return int(self.combo.get())
        except ValueError:
            return 30

    def _cargar(self):
        dias = self._dias()
        self._fill("Más vendidos", [
            (n, f"{float(c):.2f}", f"RD$ {float(t or 0):,.2f}")
            for n, c, t in self.db.reporte_mas_vendidos(dias)
        ])
        self._fill("Por cajero", [
            (u, n, f"RD$ {float(t or 0):,.2f}") for u, n, t in self.db.reporte_por_cajero(dias)
        ])
        self._fill("Por forma de pago", [
            (m, n, f"RD$ {float(t or 0):,.2f}") for m, n, t in self.db.reporte_por_forma_pago(dias)
        ])

    def _fill(self, name, rows):
        tree = self.trees[name]
        for i in tree.get_children():
            tree.delete(i)
        for r in rows:
            tree.insert("", "end", values=r)


def open_fiscal_dialog(parent, db: Database | None = None):
    db = db or Database()
    dlg = ctk.CTkToplevel(parent)
    dlg.title("Datos fiscales")
    dlg.geometry("460x420")
    dlg.transient(parent)
    dlg.grab_set()
    ctk.CTkLabel(dlg, text="RNC del emisor", font=("Arial", 12, "bold")).pack(
        anchor="w", padx=14, pady=(12, 2)
    )
    ent_rnc = ctk.CTkEntry(dlg, width=220)
    ent_rnc.pack(anchor="w", padx=14)
    ent_rnc.insert(0, db.get_config("empresa_rnc", "") or "")
    ctk.CTkLabel(
        dlg,
        text="Secuencias. El siguiente número se imprime en el ticket (B02…).",
        font=("Arial", 11),
    ).pack(anchor="w", padx=14, pady=(10, 4))
    entries = {}
    for tipo, pref, sig, hasta in db.listar_secuencias_ncf():
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(row, text=tipo, width=140, anchor="w").pack(side="left")
        e1 = ctk.CTkEntry(row, width=70)
        e1.insert(0, pref)
        e1.pack(side="left", padx=4)
        e2 = ctk.CTkEntry(row, width=80)
        e2.insert(0, str(sig))
        e2.pack(side="left", padx=4)
        e3 = ctk.CTkEntry(row, width=80)
        e3.insert(0, str(hasta))
        e3.pack(side="left", padx=4)
        entries[tipo] = (e1, e2, e3)

    def guardar():
        db.set_config("empresa_rnc", (ent_rnc.get() or "").strip())
        for tipo, (e1, e2, e3) in entries.items():
            try:
                db.guardar_secuencia_ncf(tipo, e1.get(), int(e2.get()), int(e3.get()))
            except ValueError:
                messagebox.showerror("NCF", f"Números inválidos en {tipo}.", parent=dlg)
                return
        messagebox.showinfo("NCF", "Datos fiscales guardados.", parent=dlg)
        dlg.destroy()

    ctk.CTkButton(dlg, text="Guardar", fg_color="#15803d", command=guardar).pack(pady=12)


def abrir_transferencia(parent, db: Database, producto_id: int, usuario=None):
    dlg = ctk.CTkToplevel(parent)
    dlg.title("Transferir entre bodegas")
    dlg.geometry("420x220")
    dlg.transient(parent)
    dlg.grab_set()
    bods = db.list_bodegas_codigos()
    if "Principal" not in bods:
        bods = ["Principal"] + bods
    ctk.CTkLabel(dlg, text=f"Producto {producto_id}", font=("Arial", 13, "bold")).pack(
        anchor="w", padx=14, pady=(12, 6)
    )
    ctk.CTkLabel(dlg, text="Origen").pack(anchor="w", padx=14)
    c_ori = ctk.CTkComboBox(dlg, values=bods, width=220)
    c_ori.set(bods[0])
    c_ori.pack(anchor="w", padx=14)
    ctk.CTkLabel(dlg, text="Destino").pack(anchor="w", padx=14, pady=(6, 0))
    destino_vals = bods + ["Nueva…"]
    c_des = ctk.CTkComboBox(dlg, values=destino_vals, width=220)
    c_des.set(bods[-1] if len(bods) > 1 else bods[0])
    c_des.pack(anchor="w", padx=14)
    ent_nueva = ctk.CTkEntry(dlg, width=220, placeholder_text="Nombre de bodega nueva")
    ent_nueva.pack(anchor="w", padx=14, pady=4)
    ent_cant = ctk.CTkEntry(dlg, width=120, placeholder_text="Cantidad")
    ent_cant.pack(anchor="w", padx=14)

    def ok():
        dest = ent_nueva.get().strip() if c_des.get() == "Nueva…" else c_des.get()
        try:
            cant = float((ent_cant.get() or "").replace(",", "."))
            db.transferir_entre_bodegas(producto_id, c_ori.get(), dest, cant, usuario)
        except Exception as e:
            messagebox.showerror("Transferir", str(e), parent=dlg)
            return
        messagebox.showinfo("Transferir", "Transferencia registrada.", parent=dlg)
        dlg.destroy()

    ctk.CTkButton(dlg, text="Transferir", command=ok).pack(anchor="w", padx=14, pady=10)


def imprimir_etiquetas(parent, productos: list[dict]):
    """productos: {nombre, codigo, precio}."""
    if not productos:
        messagebox.showinfo("Etiquetas", "No hay productos para etiquetar.", parent=parent)
        return
    try:
        from reportlab.pdfgen import canvas as pdf_canvas
        from reportlab.graphics.barcode import code128
    except ImportError:
        messagebox.showerror("Etiquetas", "Falta reportlab para generar el PDF.", parent=parent)
        return
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="etiquetas_")
    os.close(fd)
    c = pdf_canvas.Canvas(path, pagesize=(612, 792))
    x0, y = 36, 740
    for i, p in enumerate(productos):
        col = i % 2
        if i and col == 0:
            y -= 120
        if y < 80:
            c.showPage()
            y = 740
        x = x0 + col * 280
        nombre = (p.get("nombre") or "")[:32]
        codigo = (p.get("codigo") or "")[:40]
        precio = float(p.get("precio") or 0)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y, nombre)
        c.setFont("Helvetica", 9)
        c.drawString(x, y - 14, f"RD$ {precio:.2f}")
        if codigo:
            try:
                bar = code128.Code128(codigo, barHeight=28, barWidth=0.8)
                bar.drawOn(c, x, y - 58)
            except Exception:
                c.drawString(x, y - 32, codigo)
            c.drawString(x, y - 70, codigo)
    c.save()
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])
    messagebox.showinfo("Etiquetas", f"PDF generado:\n{path}", parent=parent)


def enviar_pdf_factura(parent, db: Database, factura_id: int, email: str, numero: str):
    from report_pdf_builder import build_factura_comprobante_pdf

    data = build_factura_comprobante_pdf(db, factura_id)
    if not data:
        messagebox.showerror("Correo", "No se pudo armar el PDF.", parent=parent)
        return
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix=f"factura_{numero}_")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    email = (email or "").replace('"', "")
    numero = (numero or str(factura_id)).replace('"', "")
    if sys.platform == "darwin":
        safe_path = path.replace('"', "")
        script = f'''
        tell application "Mail"
            set msg to make new outgoing message with properties {{subject:"Comprobante {numero}", visible:true}}
            tell msg
                make new to recipient at end of to recipients with properties {{address:"{email}"}}
                make new attachment with properties {{file name:POSIX file "{safe_path}"}}
            end tell
            activate
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", script], check=True)
            return
        except Exception:
            pass
    import webbrowser
    from urllib.parse import urlencode

    webbrowser.open(
        "mailto:"
        + email
        + "?"
        + urlencode({"subject": f"Comprobante {numero}", "body": f"PDF del comprobante:\n{path}\n"})
    )
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    messagebox.showinfo(
        "Correo",
        f"No se pudo adjuntar solo. El PDF está en:\n{path}",
        parent=parent,
    )
