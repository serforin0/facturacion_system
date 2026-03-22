import copy
import os
import socket
from datetime import datetime

import customtkinter as ctk
from PIL import Image
from tkinter import colorchooser, messagebox

from config_impresion_dialog import open_config_impresion
from dashboard_config_store import (
    _normalize_slots,
    _slot_count,
    default_config,
    load_config,
    save_config,
)


class HomeDashboardManager:
    """Panel de inicio con rejilla fija, cabecera tipo ERP y pie con equipo local."""

    _CARD_H = 84

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.main_frame = None
        self._config = load_config()
        self._icon_refs = []
        self._build()

    def _contact_lines(self, text: str):
        if not (text or "").strip():
            return []
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _layout_visible_cells(self):
        """Lista de longitud N: módulo o None (hueco vacío). Solo módulos visibles por rol."""
        c = self._config
        n = _slot_count(c)
        visible = self._visible_modules()
        placed = {}
        used = set()

        for m in visible:
            s = m.get("slot")
            try:
                si = int(s)
            except (TypeError, ValueError):
                si = None
            if si is not None and 0 <= si < n and si not in used:
                placed[si] = m
                used.add(si)

        placed_ids = {m["id"] for m in placed.values()}
        leftover = [m for m in visible if m.get("id") not in placed_ids]
        for m in leftover:
            for i in range(n):
                if i not in used:
                    placed[i] = m
                    used.add(i)
                    break

        return [placed.get(i) for i in range(n)]

    def _build(self):
        if self.main_frame is not None:
            self.main_frame.destroy()

        c = self._config
        panel_bg = c["background_color"]
        self.main_frame = ctk.CTkFrame(self.parent, fg_color=panel_bg)
        self.main_frame.pack(fill="both", expand=True)

        header = ctk.CTkFrame(self.main_frame, fg_color=panel_bg)
        header.pack(fill="x", padx=24, pady=(24, 6))

        title = ctk.CTkLabel(
            header,
            text=c["company_name"],
            font=("Arial", 24, "bold"),
            text_color=c["header_text_color"],
        )
        title.pack()

        for line in self._contact_lines(c.get("company_subtitle", "")):
            ctk.CTkLabel(
                header,
                text=line,
                font=("Arial", 13),
                text_color=c["header_text_color"],
            ).pack(pady=(2, 0))

        fecha = datetime.now().strftime("%d/%m/%Y")
        ctk.CTkLabel(
            header,
            text=fecha,
            font=("Arial", 12),
            text_color=c["header_text_color"],
        ).pack(pady=(10, 0))

        grid_wrap = ctk.CTkFrame(self.main_frame, fg_color=panel_bg)
        grid_wrap.pack(fill="both", expand=True, padx=28, pady=12)

        try:
            cols = int(c.get("grid_columns") or 3)
        except (TypeError, ValueError):
            cols = 3
        try:
            rows = int(c.get("grid_rows") or 4)
        except (TypeError, ValueError):
            rows = 4
        cols = max(1, min(cols, 6))
        rows = max(1, min(rows, 8))

        row_min = self._CARD_H + 20
        for j in range(cols):
            grid_wrap.columnconfigure(j, weight=1, minsize=130)
        for r in range(rows):
            grid_wrap.rowconfigure(r, weight=1, minsize=row_min)

        cells = self._layout_visible_cells()
        self._icon_refs = []

        bw = c.get("card_border_width")
        try:
            bw = int(bw) if bw is not None else 1
        except (TypeError, ValueError):
            bw = 1

        for idx, mod in enumerate(cells):
            r, col = divmod(idx, cols)
            if r >= rows:
                break
            cell = ctk.CTkFrame(grid_wrap, fg_color=panel_bg)
            cell.grid(row=r, column=col, padx=8, pady=8, sticky="nsew")
            if mod is None:
                self._empty_slot(cell, c, bw, panel_bg)
            else:
                btn = self._make_module_button(cell, mod, c, bw)
                btn.pack(fill="both", expand=True)

        footer = ctk.CTkFrame(self.main_frame, fg_color=panel_bg)
        footer.pack(fill="x", padx=20, pady=(8, 14))

        center = ctk.CTkFrame(footer, fg_color=panel_bg)
        center.pack(fill="x")

        hint_raw = (c.get("footer_hint") or "").strip()
        if c.get("footer_uppercase", True):
            hint_raw = hint_raw.upper()
        ctk.CTkLabel(
            center,
            text=hint_raw,
            font=("Arial", 12, "bold"),
            text_color=c["header_text_color"],
        ).pack()

        if c.get("show_hostname", True):
            try:
                host = socket.gethostname()
            except OSError:
                host = ""
            if host:
                ctk.CTkLabel(
                    center,
                    text=host,
                    font=("Arial", 11),
                    text_color=c["header_text_color"],
                ).pack(pady=(4, 0))

        bar = ctk.CTkFrame(footer, fg_color=panel_bg)
        bar.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(
            bar,
            text="⚙ Apariencia del inicio",
            width=168,
            height=28,
            font=("Arial", 12),
            fg_color=c["accent_color"],
            hover_color=self._darken_hex(c["accent_color"], 0.88),
            command=self._open_customize,
        ).pack(side="right", padx=4)

        self.main_frame.update_idletasks()

    @staticmethod
    def _darken_hex(hex_color: str, factor: float) -> str:
        try:
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            r = int(r * factor)
            g = int(g * factor)
            b = int(b * factor)
            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, IndexError):
            return "#4338CA"

    def _empty_slot(self, parent, c: dict, border_w: int, panel_bg: str):
        bc = c.get("empty_slot_border_color") or "#94A3B8"
        fr = ctk.CTkFrame(
            parent,
            fg_color=panel_bg,
            corner_radius=12,
            border_width=max(1, border_w),
            border_color=bc,
            height=self._CARD_H,
        )
        fr.pack(fill="both", expand=True)

    def _visible_modules(self):
        role = getattr(self.app, "current_role", None) or ""
        out = []
        for m in self._config.get("modules", []):
            if not m.get("visible", True):
                continue
            req = (m.get("require_role") or "").strip()
            if req and role != req:
                continue
            out.append(m)
        return out

    def _dispatch(self, action: str):
        a = (action or "").strip()
        if a == "facturacion":
            self.app.show_facturacion()
        elif a == "inventario":
            self.app.show_inventory()
        elif a == "kardex":
            self.app.show_kardex()
        elif a == "reportes":
            self.app.show_reportes()
        elif a == "caja":
            self.app.show_caja()
        elif a == "historial_facturas":
            self.app.show_historial_facturas()
        elif a == "usuarios":
            self.app.show_usuarios()
        elif a == "printer":
            open_config_impresion(
                self.app.root,
                self.app.db,
                on_applied=self.app.refresh_branding,
            )
        elif a == "indicadores":
            self.app.show_indicadores()
        else:
            messagebox.showinfo("Módulo", "Acción no configurada.")

    def _make_module_button(self, parent, mod: dict, c: dict, border_w: int):
        path = (mod.get("image_path") or "").strip()
        img = None
        if path and os.path.isfile(path):
            try:
                pil = Image.open(path)
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(48, 48))
                self._icon_refs.append(img)
            except OSError:
                img = None

        label = mod.get("label") or "Módulo"
        bc = c.get("card_border_color") or "#E2E8F0"
        common = dict(
            anchor="w",
            height=self._CARD_H,
            font=("Arial", 15, "bold"),
            fg_color=c["card_color"],
            hover_color=c["card_hover_color"],
            text_color=c["card_text_color"],
            border_width=max(0, border_w),
            border_color=bc,
            corner_radius=12,
            command=lambda m=mod: self._dispatch(m.get("action", "")),
        )

        if img is not None:
            return ctk.CTkButton(
                parent,
                text=f"  {label}",
                image=img,
                compound="left",
                **common,
            )

        icon = (mod.get("icon") or "▪").strip() or "▪"
        return ctk.CTkButton(
            parent,
            text=f"   {icon}   {label}",
            **common,
        )

    def _open_customize(self):
        c = copy.deepcopy(self._config)
        n_slots = _slot_count(c)
        dlg = ctk.CTkToplevel(self.app.root)
        dlg.title("Apariencia del panel de inicio")
        dlg.geometry("700x620")
        dlg.resizable(True, True)

        tab = ctk.CTkTabview(dlg)
        tab.pack(fill="both", expand=True, padx=12, pady=12)
        tab.add("Colores y textos")
        tab.add("Módulos")

        f0 = tab.tab("Colores y textos")

        def row_color(parent, label, key, row):
            ctk.CTkLabel(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            var = ctk.StringVar(value=c.get(key, "#000000"))

            def pick():
                ch = colorchooser.askcolor(initialcolor=var.get(), title=label)
                if ch and ch[1]:
                    var.set(ch[1])

            e = ctk.CTkEntry(parent, textvariable=var, width=120)
            e.grid(row=row, column=1, padx=4, pady=4)
            ctk.CTkButton(parent, text="Elegir…", width=80, command=pick).grid(
                row=row, column=2, padx=4, pady=4
            )
            return var

        color_keys = [
            ("Fondo del panel", "background_color"),
            ("Texto cabecera / pie", "header_text_color"),
            ("Tarjetas (normal)", "card_color"),
            ("Tarjetas (al pasar)", "card_hover_color"),
            ("Texto en tarjetas", "card_text_color"),
            ("Borde tarjetas", "card_border_color"),
            ("Borde huecos vacíos", "empty_slot_border_color"),
            ("Acento (botones)", "accent_color"),
        ]
        vars_color = {}
        for i, (lbl, k) in enumerate(color_keys):
            vars_color[k] = row_color(f0, lbl, k, i)

        r = len(color_keys)
        ctk.CTkLabel(f0, text="Grosor borde tarjeta (px)").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        ent_bw = ctk.CTkEntry(f0, width=60)
        ent_bw.grid(row=r, column=1, sticky="w", padx=4, pady=4)
        ent_bw.insert(0, str(c.get("card_border_width", 1)))

        r += 1
        ctk.CTkLabel(f0, text="Columnas / filas rejilla").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        fr_grid = ctk.CTkFrame(f0, fg_color="transparent")
        fr_grid.grid(row=r, column=1, columnspan=2, sticky="w", padx=4, pady=4)
        ent_cols = ctk.CTkEntry(fr_grid, width=50)
        ent_cols.pack(side="left", padx=2)
        ent_cols.insert(0, str(c.get("grid_columns", 3)))
        ctk.CTkLabel(fr_grid, text="×").pack(side="left", padx=4)
        ent_rows = ctk.CTkEntry(fr_grid, width=50)
        ent_rows.pack(side="left", padx=2)
        ent_rows.insert(0, str(c.get("grid_rows", 4)))

        r += 1
        ctk.CTkLabel(f0, text="Nombre empresa").grid(row=r, column=0, sticky="nw", padx=4, pady=4)
        ent_name = ctk.CTkEntry(f0, width=360)
        ent_name.grid(row=r, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ent_name.insert(0, c.get("company_name", ""))

        r += 1
        ctk.CTkLabel(f0, text="Contacto (una línea por renglón)").grid(
            row=r, column=0, sticky="nw", padx=4, pady=4
        )
        ent_sub = ctk.CTkTextbox(f0, width=360, height=90)
        ent_sub.grid(row=r, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ent_sub.insert("1.0", c.get("company_subtitle", ""))

        r += 1
        ctk.CTkLabel(f0, text="Texto instrucción (pie)").grid(
            row=r, column=0, sticky="w", padx=4, pady=4
        )
        ent_hint = ctk.CTkEntry(f0, width=360)
        ent_hint.grid(row=r, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        ent_hint.insert(0, c.get("footer_hint", ""))

        r += 1
        v_upper = ctk.BooleanVar(value=bool(c.get("footer_uppercase", True)))
        ctk.CTkCheckBox(f0, text="Instrucción en mayúsculas", variable=v_upper).grid(
            row=r, column=1, columnspan=2, sticky="w", padx=4, pady=4
        )

        r += 1
        v_host = ctk.BooleanVar(value=bool(c.get("show_hostname", True)))
        ctk.CTkCheckBox(f0, text="Mostrar nombre del equipo en el pie", variable=v_host).grid(
            row=r, column=1, columnspan=2, sticky="w", padx=4, pady=4
        )

        f1 = tab.tab("Módulos")
        scroll = ctk.CTkScrollableFrame(f1, height=380)
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(
            scroll,
            text=f"Huecos de la rejilla: 0 … {n_slots - 1} (huecos sin módulo quedan vacíos)",
            font=("Arial", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        module_vars = []
        for m in c.get("modules", []):
            fr = ctk.CTkFrame(scroll)
            fr.pack(fill="x", pady=6)
            ctk.CTkLabel(fr, text=m.get("id", ""), width=90, anchor="w").grid(
                row=0, column=0, rowspan=3, padx=4, pady=2, sticky="nw"
            )
            v_vis = ctk.BooleanVar(value=bool(m.get("visible", True)))
            ctk.CTkCheckBox(fr, text="Visible", variable=v_vis).grid(
                row=0, column=4, rowspan=3, padx=4, pady=4
            )
            ctk.CTkLabel(fr, text="Nombre").grid(row=0, column=1, sticky="e", padx=4)
            e_lab = ctk.CTkEntry(fr, width=200)
            e_lab.grid(row=0, column=2, sticky="w", padx=4)
            e_lab.insert(0, m.get("label", ""))
            ctk.CTkLabel(fr, text="Hueco (slot)").grid(row=1, column=1, sticky="e", padx=4)
            e_slot = ctk.CTkEntry(fr, width=60)
            e_slot.grid(row=1, column=2, sticky="w", padx=4)
            if m.get("slot") is not None:
                e_slot.insert(0, str(m.get("slot", "")))
            ctk.CTkLabel(fr, text="Icono").grid(row=2, column=1, sticky="e", padx=4)
            e_ic = ctk.CTkEntry(fr, width=80)
            e_ic.grid(row=2, column=2, sticky="w", padx=4)
            e_ic.insert(0, m.get("icon", ""))
            ctk.CTkLabel(fr, text="Imagen (ruta)").grid(row=3, column=1, sticky="e", padx=4)
            e_img = ctk.CTkEntry(fr, width=300)
            e_img.grid(row=3, column=2, columnspan=3, sticky="ew", padx=4, pady=4)
            e_img.insert(0, m.get("image_path", ""))
            module_vars.append((m.get("id"), v_vis, e_lab, e_slot, e_ic, e_img))

        def guardar():
            d0 = default_config()
            for k, var in vars_color.items():
                c[k] = var.get().strip() or d0.get(k, "#000000")

            try:
                bw = int(ent_bw.get().strip() or "1")
            except ValueError:
                messagebox.showerror("Error", "Grosor de borde debe ser un número entero.")
                return
            c["card_border_width"] = max(0, bw)

            try:
                gc = int(ent_cols.get().strip() or "3")
                gr = int(ent_rows.get().strip() or "4")
            except ValueError:
                messagebox.showerror("Error", "Columnas y filas deben ser números enteros.")
                return
            c["grid_columns"] = max(1, min(gc, 6))
            c["grid_rows"] = max(1, min(gr, 8))

            c["company_name"] = ent_name.get().strip() or d0["company_name"]
            c["company_subtitle"] = ent_sub.get("1.0", "end").strip()
            c["footer_hint"] = ent_hint.get().strip()
            c["footer_uppercase"] = v_upper.get()
            c["show_hostname"] = v_host.get()

            n_new = _slot_count(c)
            by_id = {m["id"]: m for m in c["modules"]}
            for mid, v_vis, e_lab, e_slot, e_ic, e_img in module_vars:
                if mid not in by_id:
                    continue
                by_id[mid]["visible"] = v_vis.get()
                by_id[mid]["label"] = e_lab.get().strip() or by_id[mid].get("label", mid)
                raw_slot = e_slot.get().strip()
                if raw_slot == "":
                    by_id[mid]["slot"] = None
                else:
                    try:
                        si = int(raw_slot)
                    except ValueError:
                        messagebox.showerror("Error", f"Hueco inválido en módulo «{mid}».")
                        return
                    if not (0 <= si < n_new):
                        messagebox.showerror(
                            "Error",
                            f"El hueco debe estar entre 0 y {n_new - 1} (módulo «{mid}»).",
                        )
                        return
                    by_id[mid]["slot"] = si
                by_id[mid]["icon"] = e_ic.get().strip()
                by_id[mid]["image_path"] = e_img.get().strip()

            _normalize_slots(c)
            save_config(c)
            self._config = load_config()
            dlg.destroy()
            self._build()

        bf = ctk.CTkFrame(dlg, fg_color="transparent")
        bf.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(bf, text="Guardar y aplicar", command=guardar).pack(side="right", padx=4)
        ctk.CTkButton(bf, text="Cancelar", fg_color="#6B7280", command=dlg.destroy).pack(
            side="right", padx=4
        )

        dlg.grab_set()
        dlg.focus_set()
