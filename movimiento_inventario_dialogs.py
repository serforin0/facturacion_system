"""Diálogos Recibir y Retirar productos (kardex / inventario)."""

from tkinter import messagebox

import customtkinter as ctk


def _parse_float(txt, default=None):
    try:
        return float((txt or "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


class RecibirProductoDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        db,
        producto_id: int,
        *,
        nombre_producto: str,
        bodegas: list,
        bodega_default: str,
        usuario: str = None,
        on_done=None,
    ):
        super().__init__(master)
        self.db = db
        self.producto_id = producto_id
        self._usuario = usuario
        self.on_done = on_done
        self.title("Recibir productos")
        self.geometry("520x480")
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(
            self,
            text=f"Producto: {nombre_producto[:72]}",
            font=("Arial", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=8)
        grid.grid_columnconfigure(1, weight=1)

        def add_row(r, label, widget):
            ctk.CTkLabel(grid, text=label, width=150, anchor="w").grid(
                row=r, column=0, padx=(0, 8), pady=6, sticky="w"
            )
            widget.grid(row=r, column=1, sticky="ew", pady=6)

        prov_rows = self.db.list_proveedores()
        nombres_prov = [p[1] for p in prov_rows]
        if not nombres_prov:
            nombres_prov = ["(Sin proveedor asignado)"]

        self.combo_prov = ctk.CTkComboBox(
            grid, values=nombres_prov, width=320, height=28
        )
        self.combo_prov.set(nombres_prov[0])
        add_row(0, "Proveedor:", self.combo_prov)

        bod_vals = list(bodegas) if bodegas else ["Principal"]
        self.combo_bodega = ctk.CTkComboBox(grid, values=bod_vals, width=320, height=28)
        if bodega_default and bodega_default in bod_vals:
            self.combo_bodega.set(bodega_default)
        else:
            self.combo_bodega.set(bod_vals[0])
        add_row(1, "Bodega:", self.combo_bodega)

        self.ent_doc = ctk.CTkEntry(
            grid, placeholder_text="Nº orden, factura proveedor…", width=320, height=28
        )
        add_row(2, "Documento / ref.:", self.ent_doc)

        self.ent_cant = ctk.CTkEntry(grid, placeholder_text="Cantidad", width=320, height=28)
        add_row(3, "Cantidad:", self.ent_cant)

        self.ent_costo = ctk.CTkEntry(
            grid, placeholder_text="Costo unitario", width=320, height=28
        )
        add_row(4, "Costo unit. $:", self.ent_costo)

        self.var_actualizar_costo = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            grid,
            text="Actualizar costo del producto (precio base) con este valor",
            variable=self.var_actualizar_costo,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ctk.CTkLabel(self, text="Notas (opcional):", anchor="w").pack(
            anchor="w", padx=14, pady=(8, 2)
        )
        self.ent_notas = ctk.CTkTextbox(self, height=72)
        self.ent_notas.pack(fill="x", padx=14, pady=(0, 8))

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", pady=12, padx=14)
        ctk.CTkButton(
            bf, text="Cancelar", fg_color="#6B7280", width=120, command=self.destroy
        ).pack(side="right", padx=6)
        ctk.CTkButton(bf, text="Guardar entrada", width=140, command=self._guardar).pack(
            side="right", padx=6
        )

    def _guardar(self):
        cant = _parse_float(self.ent_cant.get())
        if cant is None or cant <= 0:
            messagebox.showerror("Validación", "Indique una cantidad mayor que cero.", parent=self)
            return
        costo = _parse_float(self.ent_costo.get(), 0.0)
        if costo is None or costo < 0:
            messagebox.showerror("Validación", "El costo debe ser un número ≥ 0.", parent=self)
            return

        prov_name = self.combo_prov.get()
        entidad = (prov_name or "").strip()
        bodega = (self.combo_bodega.get() or "").strip() or None
        doc = (self.ent_doc.get() or "").strip()
        notas = (self.ent_notas.get("1.0", "end") or "").strip()

        if doc:
            desc = f"Orden de compra / recepción — {doc}"
        else:
            desc = "Recepción de mercancía"
        if notas:
            desc = f"{desc} — {notas[:300]}"

        ref = doc if doc else None

        try:
            self.db.insert_movimiento_kardex(
                self.producto_id,
                "ingreso",
                cant,
                ajustar_stock=True,
                referencia=ref,
                usuario=self._usuario,
                tipo_codigo="CO",
                entidad_nombre=entidad or None,
                bodega_codigo=bodega,
                precio_unitario=costo if costo else None,
                descripcion_mov=desc[:900],
            )
            if self.var_actualizar_costo.get() and costo > 0:
                conn = self.db.get_connection()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE productos SET precio_base = ? WHERE id = ?",
                    (costo, self.producto_id),
                )
                conn.commit()
                conn.close()
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return

        if self.on_done:
            self.on_done()
        self.destroy()


class RetirarProductoDialog(ctk.CTkToplevel):
    MOTIVOS = (
        "Merma / daño",
        "Uso interno",
        "Muestra",
        "Ajuste de inventario",
        "Otro",
    )

    def __init__(
        self,
        master,
        db,
        producto_id: int,
        *,
        nombre_producto: str,
        bodegas: list,
        bodega_default: str,
        stock_actual: float,
        usuario: str = None,
        on_done=None,
    ):
        super().__init__(master)
        self.db = db
        self.producto_id = producto_id
        self._usuario = usuario
        self._stock = float(stock_actual)
        self.on_done = on_done
        self.title("Retirar productos")
        self.geometry("480x400")
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(
            self,
            text=f"Producto: {nombre_producto[:72]}",
            font=("Arial", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            self,
            text=f"Stock disponible: {self._stock:.2f}",
            text_color="#94A3B8",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=8)
        grid.grid_columnconfigure(1, weight=1)

        def add_row(r, label, widget):
            ctk.CTkLabel(grid, text=label, width=150, anchor="w").grid(
                row=r, column=0, padx=(0, 8), pady=6, sticky="w"
            )
            widget.grid(row=r, column=1, sticky="ew", pady=6)

        self.combo_motivo = ctk.CTkComboBox(
            grid, values=list(self.MOTIVOS), width=300, height=28
        )
        self.combo_motivo.set(self.MOTIVOS[0])
        add_row(0, "Motivo:", self.combo_motivo)

        bod_vals = list(bodegas) if bodegas else ["Principal"]
        self.combo_bodega = ctk.CTkComboBox(grid, values=bod_vals, width=300, height=28)
        if bodega_default and bodega_default in bod_vals:
            self.combo_bodega.set(bodega_default)
        else:
            self.combo_bodega.set(bod_vals[0])
        add_row(1, "Bodega:", self.combo_bodega)

        self.ent_cant = ctk.CTkEntry(grid, placeholder_text="Cantidad a retirar", width=300, height=28)
        add_row(2, "Cantidad:", self.ent_cant)

        self.ent_ref = ctk.CTkEntry(
            grid, placeholder_text="Referencia interna (opcional)", width=300, height=28
        )
        add_row(3, "Referencia:", self.ent_ref)

        ctk.CTkLabel(self, text="Notas:", anchor="w").pack(anchor="w", padx=14, pady=(4, 2))
        self.ent_notas = ctk.CTkTextbox(self, height=56)
        self.ent_notas.pack(fill="x", padx=14, pady=(0, 8))

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", pady=12, padx=14)
        ctk.CTkButton(
            bf, text="Cancelar", fg_color="#6B7280", width=120, command=self.destroy
        ).pack(side="right", padx=6)
        ctk.CTkButton(bf, text="Registrar salida", width=140, command=self._guardar).pack(
            side="right", padx=6
        )

    def _guardar(self):
        cant = _parse_float(self.ent_cant.get())
        if cant is None or cant <= 0:
            messagebox.showerror("Validación", "Indique una cantidad mayor que cero.", parent=self)
            return
        if cant > self._stock + 1e-9:
            messagebox.showerror(
                "Stock insuficiente",
                f"No puede retirar {cant}: stock actual {self._stock:.2f}.",
                parent=self,
            )
            return

        motivo = self.combo_motivo.get()
        bodega = (self.combo_bodega.get() or "").strip() or None
        ref = (self.ent_ref.get() or "").strip()
        notas = (self.ent_notas.get("1.0", "end") or "").strip()
        desc = f"Retiro — {motivo}"
        if ref:
            desc += f" — Ref: {ref}"
        if notas:
            desc += f" — {notas[:200]}"

        try:
            self.db.insert_movimiento_kardex(
                self.producto_id,
                "retiro",
                -cant,
                ajustar_stock=True,
                referencia=ref or None,
                usuario=self._usuario,
                tipo_codigo="RT",
                entidad_nombre=motivo,
                bodega_codigo=bodega,
                precio_unitario=None,
                descripcion_mov=desc[:900],
            )
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return

        if self.on_done:
            self.on_done()
        self.destroy()
