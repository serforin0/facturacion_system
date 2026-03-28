"""
Devoluciones con nota de crédito e ingreso a inventario (flujo propio del sistema).
"""
from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox, ttk

from database import Database


class DevolucionesFacturacionFrame(ctk.CTkFrame):
    def __init__(self, parent, *, current_user: str | None = None):
        super().__init__(parent, fg_color="#1e293b")
        self.db = Database()
        self.current_user = current_user
        self._factura_id: int | None = None

        ctk.CTkLabel(
            self,
            text="Abonos por devolución",
            font=("Arial", 15, "bold"),
            text_color="white",
        ).pack(anchor="w", padx=12, pady=(12, 4))

        ctk.CTkLabel(
            self,
            text="Seleccione una factura emitida, indique cantidades a devolver y registre la nota de crédito. "
            "El stock vuelve al almacén automáticamente.",
            font=("Arial", 11),
            text_color="#94a3b8",
            wraplength=640,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        bar = ctk.CTkFrame(self, fg_color="#0f172a")
        bar.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(bar, text="Nº factura / ID").pack(side="left", padx=6)
        self.ent_buscar = ctk.CTkEntry(bar, width=160, placeholder_text="Ej: F-2026… o id")
        self.ent_buscar.pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Cargar", width=90, command=self._cargar_factura).pack(
            side="left", padx=8
        )

        mid = ctk.CTkFrame(self, fg_color="#0f172a")
        mid.pack(fill="both", expand=True, padx=8, pady=8)

        cols = ("desc", "vendido", "disponible", "devolver")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=10)
        self._det_ids: dict[str, int] = {}
        for c, t, w in (
            ("desc", "Producto", 280),
            ("vendido", "Facturado", 80),
            ("disponible", "Puede devolver", 100),
            ("devolver", "A devolver", 90),
        ):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        self.tree.column("desc", anchor="w")
        sy = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sy.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._editar_celda)

        ctk.CTkLabel(self, text="Motivo", font=("Arial", 11)).pack(anchor="w", padx=12)
        self.txt_motivo = ctk.CTkTextbox(self, height=56, font=("Arial", 11))
        self.txt_motivo.pack(fill="x", padx=12, pady=4)

        ctk.CTkButton(
            self,
            text="Registrar nota de crédito",
            fg_color="#b45309",
            height=36,
            command=self._registrar,
        ).pack(pady=12)

    def _resolver_factura_id(self) -> int | None:
        raw = (self.ent_buscar.get() or "").strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        conn = self.db.get_connection()
        cur = conn.cursor()
        digits = "".join(c for c in raw if c.isdigit())
        if digits:
            cur.execute(
                """
                SELECT id FROM facturas
                WHERE REPLACE(REPLACE(IFNULL(numero,''),'-',''),' ','') LIKE ?
                  AND IFNULL(estado,'') = 'emitida'
                LIMIT 1
                """,
                (f"%{digits}%",),
            )
        else:
            cur.execute(
                "SELECT id FROM facturas WHERE numero LIKE ? AND IFNULL(estado,'') = 'emitida' LIMIT 1",
                (f"%{raw}%",),
            )
        row = cur.fetchone()
        conn.close()
        return int(row[0]) if row else None

    def _cargar_factura(self):
        fid = self._resolver_factura_id()
        if not fid:
            messagebox.showwarning("Devolución", "No se encontró la factura emitida.")
            return
        self._factura_id = fid
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._det_ids.clear()
        lineas = self.db.get_lineas_factura_para_devolucion(fid)
        if not lineas:
            messagebox.showinfo(
                "Devolución",
                "No hay líneas con producto o ya no queda cantidad por devolver.",
            )
            return
        for det_id, desc, vend, disp, _pid in lineas:
            iid = str(det_id)
            self._det_ids[iid] = det_id
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=((desc or "")[:50], f"{vend:.2f}", f"{disp:.2f}", "0"),
            )

    def _editar_celda(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        vals = list(self.tree.item(iid, "values"))
        try:
            max_q = float(vals[2])
        except ValueError:
            return
        top = ctk.CTkToplevel(self.winfo_toplevel())
        top.title("Cantidad a devolver")
        top.geometry("300x140")
        top.transient(self.winfo_toplevel())
        top.grab_set()
        ctk.CTkLabel(top, text=f"Máximo: {max_q:.2f}").pack(pady=8)
        ent = ctk.CTkEntry(top, width=120)
        ent.pack(pady=4)
        ent.insert(0, vals[3])

        def ok():
            try:
                q = float((ent.get() or "0").replace(",", ""))
            except ValueError:
                return
            if q < 0 or q > max_q + 0.0001:
                messagebox.showwarning("Cantidad", "Valor fuera de rango.", parent=top)
                return
            vals[3] = f"{q:.2f}"
            self.tree.item(iid, values=vals)
            top.destroy()

        ctk.CTkButton(top, text="OK", command=ok).pack(pady=8)

    def _registrar(self):
        if not self._factura_id:
            messagebox.showwarning("Devolución", "Cargue primero una factura.")
            return
        motivo = self.txt_motivo.get("1.0", "end").strip()
        lineas: list[tuple[int, float]] = []
        for iid in self.tree.get_children():
            try:
                q = float(self.tree.item(iid, "values")[3])
            except (ValueError, IndexError):
                continue
            if q > 0.0001:
                lineas.append((int(iid), q))
        if not lineas:
            messagebox.showwarning("Devolución", "Indique al menos una cantidad a devolver.")
            return
        ok, msg = self.db.registrar_devolucion_nota_credito(
            self._factura_id, lineas, motivo, self.current_user
        )
        if ok:
            messagebox.showinfo("Nota de crédito", msg)
            self._cargar_factura()
            self.txt_motivo.delete("1.0", "end")
        else:
            messagebox.showwarning("Devolución", msg)
