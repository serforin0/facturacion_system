"""
Configuración de empresa (reportes), apariencia (logo / nombre) y perfil de impresora.
"""
import os
import sys
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from database import Database
from styles import Styles


class ConfigImpresionDialog(ctk.CTkToplevel):
    def __init__(self, master, db: Database, on_applied=None):
        super().__init__(master)
        self.db = db
        self._on_applied = on_applied
        self.title("Configuración — empresa y apariencia")
        self.geometry("520x780")
        self.resizable(True, True)
        self.configure(fg_color=("#ECE8E2", "#2B2B2B"))
        self.transient(master.winfo_toplevel())
        self.grab_set()

        emp = db.get_empresa_info()
        prof, w58 = db.get_printer_profile()
        w80_s = db.get_config("printer_width_epson_80", "42")
        try:
            w80 = int(w80_s)
        except (TypeError, ValueError):
            w80 = 42
        try:
            w58 = int(w58)
        except (TypeError, ValueError):
            w58 = 32

        pad = {"padx": 14, "pady": 6}
        ctk.CTkLabel(
            self,
            text="Apariencia (login y barra superior)",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", **pad)

        ui_mode_lbl = {
            "dark": "Oscuro",
            "light": "Claro",
            "system": "Sistema",
        }
        ui_theme_lbl = {
            "blue": "Azul",
            "green": "Verde",
            "dark-blue": "Azul oscuro",
        }
        cur_mode = (self.db.get_config("ui_appearance_mode", "dark") or "dark").strip()
        if cur_mode not in ui_mode_lbl:
            cur_mode = "dark"
        cur_theme = (self.db.get_config("ui_color_theme", "blue") or "blue").strip()
        if cur_theme not in ui_theme_lbl:
            cur_theme = "blue"

        ctk.CTkLabel(
            self,
            text="Modo de ventana (claro / oscuro) y color de acento:",
            font=("Arial", 11),
        ).pack(anchor="w", padx=14)
        row_ui = ctk.CTkFrame(self, fg_color="transparent")
        row_ui.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(row_ui, text="Modo:", width=120, anchor="w").pack(
            side="left", padx=(0, 8)
        )
        self.combo_ui_mode = ctk.CTkComboBox(
            row_ui,
            width=200,
            values=list(ui_mode_lbl.values()),
        )
        self.combo_ui_mode.set(ui_mode_lbl[cur_mode])
        self.combo_ui_mode.pack(side="left", padx=4)
        ctk.CTkLabel(row_ui, text="Tema:", width=56, anchor="w").pack(
            side="left", padx=(16, 8)
        )
        self.combo_ui_theme = ctk.CTkComboBox(
            row_ui,
            width=180,
            values=list(ui_theme_lbl.values()),
        )
        self.combo_ui_theme.set(ui_theme_lbl[cur_theme])
        self.combo_ui_theme.pack(side="left", padx=4)
        ctk.CTkLabel(
            self,
            text="Se aplica al guardar (también afecta botones y cuadros del sistema).",
            font=("Arial", 10),
            text_color="gray",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        ctk.CTkLabel(
            self,
            text="Logo (PNG/JPG). Vacío = se usa assets/logo.png si existe.",
            font=("Arial", 11),
            text_color="gray",
        ).pack(anchor="w", padx=14)
        lf = ctk.CTkFrame(self, fg_color="transparent")
        lf.pack(fill="x", padx=14, pady=4)
        self.ent_logo = ctk.CTkEntry(lf, width=360, placeholder_text="Ruta del archivo de imagen…")
        self.ent_logo.pack(side="left", padx=(0, 8))
        lp = self.db.get_config("app_logo_path", "") or ""
        if lp:
            self.ent_logo.insert(0, lp)

        def _browse_logo():
            p = filedialog.askopenfilename(
                parent=self,
                title="Elegir logo",
                filetypes=[
                    ("Imágenes", "*.png *.jpg *.jpeg *.webp *.gif"),
                    ("Todos", "*.*"),
                ],
            )
            if p:
                self.ent_logo.delete(0, "end")
                self.ent_logo.insert(0, p)

        ctk.CTkButton(lf, text="Buscar…", width=88, command=_browse_logo).pack(side="left")

        self._logo_preview = None
        self.lbl_logo_prev = ctk.CTkLabel(self, text="")
        self.lbl_logo_prev.pack(pady=6)

        def _prev_logo():
            raw = self.ent_logo.get().strip()
            path = raw if raw and os.path.isfile(raw) else None
            if not path:
                path = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
            try:
                pil = Image.open(path).convert("RGBA")
                pil.thumbnail((120, 120))
                self._logo_preview = ctk.CTkImage(
                    light_image=pil, dark_image=pil, size=pil.size
                )
                self.lbl_logo_prev.configure(image=self._logo_preview, text="")
            except OSError:
                self.lbl_logo_prev.configure(image=None, text="(sin vista previa)")

        ctk.CTkButton(lf, text="Vista previa", width=100, command=_prev_logo).pack(
            side="left", padx=6
        )
        self.after(100, _prev_logo)

        ctk.CTkLabel(
            self,
            text="Datos de empresa (reportes, tickets y título de la ventana)",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", padx=14, pady=(14, 4))

        ctk.CTkLabel(
            self,
            text="Nombre de la empresa o proyecto (cliente final):",
            font=("Arial", 11),
        ).pack(anchor="w", padx=14)
        self.ent_nombre = ctk.CTkEntry(self, width=460)
        self.ent_nombre.pack(padx=14, pady=4)
        self.ent_nombre.insert(0, emp["nombre"])

        ctk.CTkLabel(self, text="Dirección (varias líneas permitidas):").pack(
            anchor="w", padx=14, pady=(10, 0)
        )
        self.txt_dir = ctk.CTkTextbox(self, height=72)
        self.txt_dir.pack(fill="x", padx=14, pady=4)
        if emp["direccion"]:
            self.txt_dir.insert("1.0", emp["direccion"])

        ctk.CTkLabel(
            self,
            text="Ticket / factura térmica",
            font=("Arial", 14, "bold"),
        ).pack(anchor="w", padx=14, pady=(16, 4))

        self.var_perfil = ctk.StringVar(value=prof)
        rf = ctk.CTkFrame(self, fg_color="transparent")
        rf.pack(fill="x", padx=14)
        ctk.CTkRadioButton(
            rf,
            text="Móvil 58 mm (rollo pequeño)",
            variable=self.var_perfil,
            value="movil_58",
        ).pack(anchor="w", pady=2)
        ctk.CTkRadioButton(
            rf,
            text="Térmica 80 mm (Epson / estándar)",
            variable=self.var_perfil,
            value="epson_80",
        ).pack(anchor="w", pady=2)

        ctk.CTkLabel(
            self,
            text="Caracteres por línea (ajuste fino si el texto se corta o sobra):",
            font=("Arial", 11),
        ).pack(anchor="w", padx=14, pady=(8, 0))

        gf = ctk.CTkFrame(self, fg_color="transparent")
        gf.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(gf, text="58 mm:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.sp_58 = ctk.CTkSlider(gf, from_=24, to=48, number_of_steps=24, width=200)
        self.sp_58.set(w58)
        self.sp_58.grid(row=0, column=1, sticky="w")
        self.lbl_58 = ctk.CTkLabel(gf, text=str(w58), width=36)
        self.lbl_58.grid(row=0, column=2, padx=6)
        ctk.CTkLabel(gf, text="80 mm:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        self.sp_80 = ctk.CTkSlider(gf, from_=32, to=56, number_of_steps=24, width=200)
        self.sp_80.set(w80)
        self.sp_80.grid(row=1, column=1, sticky="w", pady=6)
        self.lbl_80 = ctk.CTkLabel(gf, text=str(w80), width=36)
        self.lbl_80.grid(row=1, column=2, padx=6)

        def upd58(_=None):
            self.lbl_58.configure(text=str(int(self.sp_58.get())))

        def upd80(_=None):
            self.lbl_80.configure(text=str(int(self.sp_80.get())))

        self.sp_58.configure(command=upd58)
        self.sp_80.configure(command=upd80)

        ctk.CTkLabel(
            self,
            text="Windows: nombre de impresora (opcional, vacío = predeterminada del sistema):",
            font=("Arial", 10),
            wraplength=480,
        ).pack(anchor="w", padx=14, pady=(12, 0))
        self.ent_printer = ctk.CTkEntry(self, width=460, placeholder_text="Ej: EPSON TM-T20III")
        self.ent_printer.pack(padx=14, pady=4)
        pn = self.db.get_config("printer_name", "") or ""
        if pn:
            self.ent_printer.insert(0, pn)

        if not sys.platform.startswith("win"):
            self.ent_printer.configure(state="disabled")

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(fill="x", padx=14, pady=20)
        ctk.CTkButton(
            bf, text="Guardar", fg_color="#2563EB", width=120, command=self._guardar
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            bf, text="Cerrar", fg_color="#6B7280", width=100, command=self.destroy
        ).pack(side="left", padx=12)

        ctk.CTkLabel(
            self,
            text="El ancho en caracteres define cómo se arma el texto del ticket antes de enviarlo\n"
            "a la impresora RAW en Windows; en Mac/Linux se usa archivo de texto para imprimir manualmente.",
            font=("Arial", 10),
            text_color="gray",
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))

    def _guardar(self):
        logo = self.ent_logo.get().strip()
        if logo and not os.path.isfile(logo):
            messagebox.showerror("Logo", "La ruta del logo no existe o no es accesible.")
            return
        self.db.set_config("app_logo_path", logo)

        inv_mode = {"Oscuro": "dark", "Claro": "light", "Sistema": "system"}
        inv_theme = {"Azul": "blue", "Verde": "green", "Azul oscuro": "dark-blue"}
        mtxt = (self.combo_ui_mode.get() or "Oscuro").strip()
        ttxt = (self.combo_ui_theme.get() or "Azul").strip()
        self.db.set_config("ui_appearance_mode", inv_mode.get(mtxt, "dark"))
        self.db.set_config("ui_color_theme", inv_theme.get(ttxt, "blue"))
        Styles.setup_theme_from_db(self.db)
        try:
            top = self.master.winfo_toplevel()
            Styles.apply_root_window_bg(top, self.db)
        except Exception:
            pass

        nombre = self.ent_nombre.get().strip() or "Mi empresa"
        direccion = self.txt_dir.get("1.0", "end").strip()
        self.db.set_empresa_info(nombre, direccion)

        prof = self.var_perfil.get()
        if prof not in ("movil_58", "epson_80"):
            prof = "movil_58"
        w58 = int(self.sp_58.get())
        w80 = int(self.sp_80.get())
        self.db.set_printer_profile(prof, width_movil_58=w58, width_epson_80=w80)
        self.db.set_config("ticket_width_chars", str(w58 if prof == "movil_58" else w80))

        if sys.platform.startswith("win"):
            pn = self.ent_printer.get().strip()
            if pn:
                self.db.set_config("printer_name", pn)
            else:
                self.db.set_config("printer_name", "")

        messagebox.showinfo("Guardado", "Configuración aplicada.")
        if callable(self._on_applied):
            try:
                self._on_applied()
            except Exception:
                pass
        self.destroy()


def open_config_impresion(master, db: Database, on_applied=None):
    ConfigImpresionDialog(master, db, on_applied=on_applied)
