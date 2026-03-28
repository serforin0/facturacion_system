"""
Catálogo rápido de productos para el punto de venta (vista clara, no réplica de pantallas legacy).
"""
from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk, messagebox

from database import Database


class PosCatalogoDialog(ctk.CTkToplevel):
    """Rejilla + búsqueda; al aceptar devuelve el id de producto elegido (o None)."""

    def __init__(self, master, db: Database, on_select):
        super().__init__(master)
        self.title("Catálogo de productos")
        self.geometry("720x460")
        self.transient(master)
        self.grab_set()
        self.db = db
        self._on_select = on_select
        self._result = None

        top = ctk.CTkFrame(self, fg_color="#1e293b")
        top.pack(fill="x", padx=8, pady=8)

        ctk.CTkLabel(top, text="Buscar", font=("Arial", 11, "bold")).pack(
            side="left", padx=4
        )
        self.ent = ctk.CTkEntry(top, width=220, placeholder_text="Código, nombre…")
        self.ent.pack(side="left", padx=4)
        self.ent.bind("<Return>", lambda e: self._cargar())

        self.var_modo = ctk.StringVar(value="contiene")
        ctk.CTkRadioButton(
            top, text="Contiene", variable=self.var_modo, value="contiene"
        ).pack(side="left", padx=6)
        ctk.CTkRadioButton(
            top, text="Empieza por", variable=self.var_modo, value="inicio"
        ).pack(side="left", padx=2)

        ctk.CTkButton(top, text="Buscar", width=90, command=self._cargar).pack(
            side="left", padx=8
        )

        fr = ctk.CTkFrame(self, fg_color="#0f172a")
        fr.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        cols = ("codigo", "nombre", "precio", "stock")
        self.tree = ttk.Treeview(fr, columns=cols, show="headings", height=14)
        for c, t, w in (
            ("codigo", "Código", 120),
            ("nombre", "Descripción", 320),
            ("precio", "Precio", 90),
            ("stock", "Stock", 70),
        ):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        sb = ttk.Scrollbar(fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self._aceptar())
        self._cargar()
        self.ent.focus_set()

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", pady=8)
        ctk.CTkButton(
            bf, text="Usar producto", fg_color="#2563eb", command=self._aceptar
        ).pack(side="left", padx=8)
        ctk.CTkButton(bf, text="Cerrar", fg_color="#64748b", command=self.destroy).pack(
            side="left", padx=4
        )

    def _cargar(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        q = (self.ent.get() or "").strip()
        modo = self.var_modo.get()
        conn = self.db.get_connection()
        cur = conn.cursor()
        if not q:
            cur.execute(
                """
                SELECT id, IFNULL(codigo_producto,''), nombre, precio, IFNULL(stock,0)
                FROM productos
                WHERE IFNULL(activo,1) = 1
                ORDER BY nombre
                LIMIT 400
                """
            )
        elif modo == "inicio":
            like = f"{q}%"
            cur.execute(
                """
                SELECT id, IFNULL(codigo_producto,''), nombre, precio, IFNULL(stock,0)
                FROM productos
                WHERE IFNULL(activo,1) = 1
                  AND (IFNULL(codigo_producto,'') LIKE ? OR nombre LIKE ?)
                ORDER BY nombre
                LIMIT 400
                """,
                (like, like),
            )
        else:
            like = f"%{q}%"
            cur.execute(
                """
                SELECT id, IFNULL(codigo_producto,''), nombre, precio, IFNULL(stock,0)
                FROM productos
                WHERE IFNULL(activo,1) = 1
                  AND (IFNULL(codigo_producto,'') LIKE ? OR nombre LIKE ?
                       OR IFNULL(codigo_barras,'') LIKE ?)
                ORDER BY nombre
                LIMIT 400
                """,
                (like, like, like),
            )
        rows = cur.fetchall()
        conn.close()
        for pid, cod, nom, pr, st in rows:
            self.tree.insert(
                "",
                "end",
                iid=str(pid),
                values=(cod or "—", (nom or "")[:56], f"{float(pr or 0):,.2f}", st),
            )

    def _aceptar(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Catálogo", "Seleccione un producto.", parent=self)
            return
        pid = int(sel[0])
        if callable(self._on_select):
            self._on_select(pid)
        self.destroy()
