import os
import threading

import customtkinter as ctk
import tkinterdnd2 as tkdnd   # lo dejamos importado por si lo usas en otros módulos
from tkinter import messagebox
from PIL import Image  # Para cargar el logo

from config_impresion_dialog import open_config_impresion
from inventory_manager import InventoryManager
from styles import Styles
from cuenta_servidor import ServidorInalcanzable, consultar_usuario, url_servidor
from database import Database
from facturacion_erp_manager import FacturacionERPManager
from factura_manager import FacturaManager              # Módulo de facturación (punto de venta)
from main_reportes_manager import MainReportesManager   # Módulo general de reportes (Ventas + Inventario)
from caja_manager import CajaManager                    # Módulo de caja
from users_manager import UsersManager                  # 🔹 Módulo de gestión de usuarios
from historial_facturas_manager import HistorialFacturasManager
from dashboard_manager import DashboardManager
from home_dashboard import HomeDashboardManager


class BarSystemApp:
    def __init__(self):
        # Base de datos antes del tema (modo claro/oscuro y color vienen de config)
        self.db = Database()
        Styles.setup_theme_from_db(self.db)

        # ✅ Ventana principal: SIEMPRE CTk
        self.root = ctk.CTk()
        self._apply_sensible_window_geometry()
        Styles.apply_root_window_bg(self.root, self.db)

        # Variables de sesión
        self.current_user = None
        self.current_role = None  # p.ej: "admin", "cajero"

        # Referencias a frames/módulos
        self.login_frame = None
        self.main_bg = None
        self.main_container = None
        self.inventory_manager = None
        self.factura_manager = None
        self.facturacion_erp_manager = None
        self.reportes_manager = None
        self.caja_manager = None
        self.users_manager = None
        self.historial_facturas_manager = None
        self.home_dashboard_manager = None
        self.lbl_main_title = None
        self.header_bar = None
        self.btn_nav_toggle = None
        self.nav_wrap = None
        self._nav_collapsed = False
        self._nav_buttons = {}
        self._nav_active = None

        emp = self.db.get_empresa_info()
        self.root.title(f"{emp.get('nombre') or 'Sistema'} — Gestión")

        # Mostrar primero el login
        self.show_login()

    def _apply_sensible_window_geometry(self):
        """
        Tamaño inicial y mínimo acordes a la pantalla (p. ej. 1366×768 o notebooks),
        para que la ventana no quede cortada y pueda redimensionarse.
        """
        self.root.update_idletasks()
        sw = max(1, self.root.winfo_screenwidth())
        sh = max(1, self.root.winfo_screenheight())
        # Espacio para barra de tareas y bordes del sistema
        mx, my = 40, 72
        avail_w = max(640, sw - mx)
        avail_h = max(400, sh - my)

        pref_w, pref_h = 1200, 700
        w = min(pref_w, avail_w)
        h = min(pref_h, avail_h)

        # Mínimos bajos para pantallas pequeñas; nunca mayores que el área útil
        min_w = max(640, min(780, avail_w))
        min_h = max(400, min(460, avail_h))

        self.root.minsize(min_w, min_h)
        self.root.resizable(True, True)

        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ==========================
    #       PANTALLA LOGIN
    # ==========================
    def show_login(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        emp = self.db.get_empresa_info()
        company = (emp.get("nombre") or "Mi empresa").strip()

        self.login_frame = ctk.CTkFrame(self.root, fg_color="#0f172a")
        self.login_frame.pack(fill="both", expand=True)

        self.login_frame.grid_columnconfigure(0, weight=1)
        self.login_frame.grid_rowconfigure(0, weight=1)
        self.login_frame.grid_rowconfigure(2, weight=1)

        card = ctk.CTkFrame(
            self.login_frame,
            fg_color="#1e293b",
            corner_radius=20,
            border_width=1,
            border_color="#334155",
        )
        card.grid(row=1, column=0, pady=24, padx=24, sticky="n")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=36, pady=32)

        logo_path = self.db.get_app_logo_path()
        if not logo_path:
            _def = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
            logo_path = _def if os.path.isfile(_def) else None

        self._login_logo_img = None
        if logo_path:
            try:
                pil = Image.open(logo_path).convert("RGBA")
                pil.thumbnail((168, 168))
                self._login_logo_img = ctk.CTkImage(
                    light_image=pil, dark_image=pil, size=pil.size
                )
                ctk.CTkLabel(inner, image=self._login_logo_img, text="").pack(pady=(0, 12))
            except OSError:
                pass

        ctk.CTkLabel(
            inner,
            text=company,
            font=("Arial", 22, "bold"),
            text_color="#f8fafc",
        ).pack(pady=(0, 4))
        ctk.CTkLabel(
            inner,
            text="Sistema de gestión",
            font=("Arial", 13),
            text_color="#94a3b8",
        ).pack(pady=(0, 20))

        ctk.CTkLabel(
            inner,
            text="Iniciar sesión",
            font=("Arial", 16, "bold"),
            text_color="#e2e8f0",
        ).pack(anchor="w", pady=(0, 12))

        self.entry_user = ctk.CTkEntry(
            inner,
            width=300,
            height=42,
            placeholder_text="Usuario",
            corner_radius=10,
        )
        self.entry_user.pack(pady=(0, 10))
        self.entry_pass = ctk.CTkEntry(
            inner,
            width=300,
            height=42,
            placeholder_text="Contraseña",
            corner_radius=10,
            show="•",
        )
        self.entry_pass.pack(pady=(0, 18))

        self.btn_login = ctk.CTkButton(
            inner,
            text="Entrar",
            width=300,
            height=44,
            corner_radius=10,
            font=("Arial", 15, "bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self.handle_login,
        )
        self.btn_login.pack(pady=(0, 8))

        self.lbl_login_estado = ctk.CTkLabel(
            inner,
            text="Si el usuario no está en esta computadora, se busca en el servidor.",
            font=("Arial", 10),
            text_color="#64748b",
            wraplength=300,
        )
        self.lbl_login_estado.pack(pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text="Configure nombre y logo en el menú principal → Apariencia",
            font=("Arial", 10),
            text_color="#64748b",
        ).pack(pady=(12, 0))

        self.root.bind("<Return>", self._on_enter_login)

    def _on_enter_login(self, event):
        self.handle_login()

    def handle_login(self):
        if getattr(self, "_login_buscando", False):
            return
        username = self.entry_user.get().strip()
        password = self.entry_pass.get().strip()

        if not username or not password:
            messagebox.showwarning("Aviso", "Ingresa usuario y contraseña")
            return

        role = self.db.validate_user(username, password)
        if role:
            self._abrir_sesion(username, role, password)
            return

        if self.db.usuario_existe(username):
            messagebox.showerror("Error", "Usuario o contraseña incorrectos")
            return

        self._login_buscando = True
        self.btn_login.configure(state="disabled", text="Buscando en el servidor...")
        self.lbl_login_estado.configure(text="Comprobando el usuario en el servidor...")

        def trabajo():
            rol = None
            error = None
            try:
                rol = consultar_usuario(url_servidor(self.db), username, password)
            except ServidorInalcanzable as exc:
                error = str(exc)
            self.root.after(0, lambda: self._aplicar_usuario_servidor(username, password, rol, error))

        threading.Thread(target=trabajo, daemon=True).start()

    def _aplicar_usuario_servidor(self, username, password, rol, error):
        self._login_buscando = False
        if not getattr(self, "btn_login", None):
            return
        self.btn_login.configure(state="normal", text="Entrar")
        self.lbl_login_estado.configure(
            text="Si el usuario no está en esta computadora, se busca en el servidor."
        )
        if error:
            messagebox.showerror("Servidor", error)
            return
        if not rol:
            messagebox.showerror("Error", "Usuario o contraseña incorrectos")
            return
        try:
            self.db.create_user(username, password, rol)
        except ValueError:
            rol = self.db.validate_user(username, password)
            if not rol:
                messagebox.showerror("Error", "El usuario ya existe en esta computadora con otra contraseña.")
                return
        self.db.set_config("pendiente_sync_datos", username)
        messagebox.showinfo(
            "Usuario guardado",
            "El usuario quedó guardado en esta computadora.\n"
            "El siguiente paso es sincronizar los datos.",
        )
        self._abrir_sesion(username, rol, password)

    def _abrir_sesion(self, username, role, password=""):
        self.current_user = username
        self.current_role = role
        self._clave_sesion = password or ""

        self.root.unbind("<Return>")
        self.login_frame.destroy()
        self.setup_ui()

    # ==========================
    #      PERMISOS / ROLES
    # ==========================
    def _user_can_manage_inventory(self) -> bool:
        """
        Devuelve True si el usuario logueado puede usar el módulo de inventario.
        Ahora mismo solo 'admin'.
        """
        return self.current_role in ("admin",)

    # ==========================
    #      UI PRINCIPAL
    # ==========================
    def refresh_branding(self):
        """Actualiza título de ventana y cabecera tras cambiar empresa/logo en configuración."""
        emp = self.db.get_empresa_info()
        name = (emp.get("nombre") or "Sistema").strip()
        self.root.title(f"{name} — Gestión")
        if getattr(self, "lbl_main_title", None) is not None:
            u = ""
            if self.current_user and self.current_role:
                u = f"   |   Usuario: {self.current_user} ({self.current_role})"
            self.lbl_main_title.configure(
                text=f"{name.upper()} — SISTEMA DE GESTIÓN{u}"
            )

    def _setup_main_nav(self):
        self._nav_buttons.clear()
        self.nav_wrap = ctk.CTkFrame(self.main_bg, fg_color="#1e293b", corner_radius=8)

        rows = ctk.CTkFrame(self.nav_wrap, fg_color="transparent")
        rows.pack(fill="x", padx=8, pady=8)

        def row():
            return ctk.CTkFrame(rows, fg_color="transparent")

        r1 = row()
        r1.pack(fill="x", pady=(0, 4))
        r2 = row()
        r2.pack(fill="x")

        def add_btn(parent, key, text, command, width=118):
            b = ctk.CTkButton(
                parent,
                text=text,
                width=width,
                height=32,
                font=("Arial", 11, "bold"),
                fg_color="#64748B",
                hover_color="#475569",
                command=command,
            )
            b.pack(side="left", padx=3, pady=2)
            self._nav_buttons[key] = b

        add_btn(r1, "home", "🏠 Inicio", self.show_dashboard)
        add_btn(r1, "facturacion", "🧾 Facturación", self.show_facturacion)
        add_btn(r1, "caja", "💵 Caja", self.show_caja)
        add_btn(r1, "historial", "📋 Historial", self.show_historial_facturas)
        if self._user_can_manage_inventory():
            add_btn(r1, "inventory", "📦 Inventario", self.show_inventory)
            add_btn(r1, "kardex", "📑 Kardex", self.show_kardex)
        add_btn(r1, "reportes", "📊 Reportes", self.show_reportes)
        add_btn(r1, "indicators", "📈 Indicadores", self.show_indicadores)

        add_btn(
            r2,
            "compras",
            "Compras",
            self.show_compras,
            width=100,
        )
        add_btn(
            r2,
            "cotizaciones",
            "Cotizaciones",
            self.show_facturacion,
            width=100,
        )
        add_btn(
            r2,
            "devoluciones",
            "Devoluciones",
            self.show_devoluciones,
            width=100,
        )
        add_btn(r2, "otros", "Promociones", self.show_promociones, width=110)
        add_btn(r2, "combos", "Combos", self.show_combos, width=90)
        add_btn(r2, "libro", "Libro", self.show_libro, width=80)
        if self._user_can_manage_inventory():
            add_btn(r2, "sync", "Sincronizar", self._abrir_sincronizacion, width=110)
        if self.current_role == "admin":
            add_btn(r2, "users", "👤 Usuarios", self.show_usuarios, width=110)
        add_btn(
            r2,
            "apariencia",
            "🎨 Apariencia",
            self._open_apariencia_config,
            width=120,
        )

    def _set_active_nav(self, key):
        self._nav_active = key
        base, hi = "#64748B", "#2563EB"
        for k, btn in self._nav_buttons.items():
            btn.configure(fg_color=hi if k == key else base)

    def _nav_stub(self, nombre: str):
        self._set_active_nav(None)
        messagebox.showinfo(
            nombre,
            "Este módulo está en la hoja de ruta.\n"
            "Use Facturación, Inventario o Reportes según corresponda.",
        )

    def _open_apariencia_config(self):
        open_config_impresion(self.root, self.db, on_applied=self.refresh_branding)

    def _toggle_main_nav(self):
        """Muestra u oculta el panel de botones del menú principal (estilo hamburguesa)."""
        self._nav_collapsed = not self._nav_collapsed
        try:
            self.db.set_config(
                "main_nav_collapsed", "1" if self._nav_collapsed else "0"
            )
        except Exception:
            pass
        self._sync_main_nav_button_and_pack()

    def _sync_main_nav_button_and_pack(self):
        """Actualiza el icono del botón y empaqueta o retira la barra de navegación."""
        if self.nav_wrap is None or self.btn_nav_toggle is None:
            return
        if self._nav_collapsed:
            self.nav_wrap.pack_forget()
            self.btn_nav_toggle.configure(text="☰")
        else:
            self.nav_wrap.pack(
                fill="x", padx=10, pady=(0, 8), before=self.main_container
            )
            self.btn_nav_toggle.configure(text="▲")

    def setup_ui(self):
        self.main_bg = ctk.CTkFrame(self.root, fg_color="#2B2B2B")
        self.main_bg.pack(fill="both", expand=True)

        self.header_bar = ctk.CTkFrame(self.main_bg, fg_color="#334155", corner_radius=6)
        self.header_bar.pack(fill="x", padx=10, pady=(10, 6))

        self.btn_nav_toggle = ctk.CTkButton(
            self.header_bar,
            text="▲",
            width=42,
            height=40,
            font=("Arial", 16),
            fg_color="#475569",
            hover_color="#2563eb",
            command=self._toggle_main_nav,
        )
        self.btn_nav_toggle.pack(side="left", padx=(10, 8), pady=8)

        self.lbl_main_title = ctk.CTkLabel(
            self.header_bar,
            text="",
            font=("Arial", 18, "bold"),
            text_color="white",
            anchor="w",
        )
        self.lbl_main_title.pack(side="left", fill="x", expand=True, padx=(4, 12), pady=10)
        self.refresh_branding()

        self._setup_main_nav()

        try:
            self._nav_collapsed = (
                self.db.get_config("main_nav_collapsed", "0") == "1"
            )
        except Exception:
            self._nav_collapsed = False

        self.main_container = ctk.CTkFrame(self.main_bg, fg_color="#2B2B2B")
        self.main_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._sync_main_nav_button_and_pack()

        self.show_dashboard()

    def clear_main_container(self):
        if self.main_container is not None:
            for widget in self.main_container.winfo_children():
                widget.destroy()
        self.inventory_manager = None
        self.factura_manager = None
        self.facturacion_erp_manager = None
        self.reportes_manager = None
        self.caja_manager = None
        self.users_manager = None
        self.historial_facturas_manager = None
        self.dashboard_manager = None
        self.home_dashboard_manager = None

    # ==========================
    #   CONFIGURACIÓN IMPRESORA
    # ==========================
    def show_printer_config(self):
        """
        Ventana para seleccionar el perfil de impresora y el ancho del ticket.
        Usa los métodos de Database:
          - get_printer_profile()
          - set_printer_profile(...)
        """
        # Valores actuales
        profile, current_width = self.db.get_printer_profile()
        width_movil = self.db.get_config("printer_width_movil_58", "32")
        width_epson = self.db.get_config("printer_width_epson_80", "42")

        try:
            width_movil_int = int(width_movil)
        except (TypeError, ValueError):
            width_movil_int = 32

        try:
            width_epson_int = int(width_epson)
        except (TypeError, ValueError):
            width_epson_int = 42

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Configuración de impresora")
        dlg.geometry("420x260")
        dlg.resizable(False, False)

        title = ctk.CTkLabel(
            dlg,
            text="Configuración de impresora / ticket",
            font=("Arial", 16, "bold")
        )
        title.pack(pady=(10, 10))

        # Perfil actual
        profile_var = ctk.StringVar(value=profile)

        frame_radios = ctk.CTkFrame(dlg)
        frame_radios.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            frame_radios,
            text="Perfil de impresora:",
            font=("Arial", 13)
        ).pack(anchor="w", padx=5, pady=(5, 5))

        radio_movil = ctk.CTkRadioButton(
            frame_radios,
            text="Impresora móvil 58mm (Bluetooth)",
            variable=profile_var,
            value="movil_58"
        )
        radio_movil.pack(anchor="w", padx=20, pady=2)

        radio_epson = ctk.CTkRadioButton(
            frame_radios,
            text="Impresora de mostrador 80mm (Epson)",
            variable=profile_var,
            value="epson_80"
        )
        radio_epson.pack(anchor="w", padx=20, pady=2)

        # Anchos
        frame_widths = ctk.CTkFrame(dlg)
        frame_widths.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            frame_widths,
            text="Ancho (caracteres por línea):",
            font=("Arial", 13)
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=(5, 5))

        ctk.CTkLabel(frame_widths, text="Móvil 58mm:").grid(
            row=1, column=0, sticky="e", padx=5, pady=3
        )
        entry_movil = ctk.CTkEntry(frame_widths, width=70)
        entry_movil.grid(row=1, column=1, sticky="w", padx=5, pady=3)
        entry_movil.insert(0, str(width_movil_int))

        ctk.CTkLabel(frame_widths, text="Epson 80mm:").grid(
            row=2, column=0, sticky="e", padx=5, pady=3
        )
        entry_epson = ctk.CTkEntry(frame_widths, width=70)
        entry_epson.grid(row=2, column=1, sticky="w", padx=5, pady=3)
        entry_epson.insert(0, str(width_epson_int))

        # Botones
        btn_frame = ctk.CTkFrame(dlg)
        btn_frame.pack(fill="x", padx=10, pady=15)

        def guardar_config():
            sel_profile = profile_var.get()
            try:
                nuevo_movil = int(entry_movil.get().strip() or "32")
            except ValueError:
                messagebox.showerror(
                    "Error",
                    "El ancho para la impresora móvil debe ser un número entero."
                )
                return

            try:
                nuevo_epson = int(entry_epson.get().strip() or "42")
            except ValueError:
                messagebox.showerror(
                    "Error",
                    "El ancho para la impresora Epson debe ser un número entero."
                )
                return

            if nuevo_movil <= 0 or nuevo_epson <= 0:
                messagebox.showerror(
                    "Error",
                    "El ancho debe ser mayor que cero."
                )
                return

            # Guardar en DB
            self.db.set_printer_profile(
                sel_profile,
                width_movil_58=nuevo_movil,
                width_epson_80=nuevo_epson
            )

            messagebox.showinfo(
                "Guardado",
                "Configuración de impresora actualizada.\n"
                "Los nuevos tickets se imprimirán con este ancho."
            )
            dlg.destroy()

        ctk.CTkButton(
            btn_frame,
            text="💾 Guardar",
            fg_color="#27ae60",
            command=guardar_config
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_frame,
            text="Cancelar",
            fg_color="#7f8c8d",
            command=dlg.destroy
        ).pack(side="right", padx=5)

        dlg.grab_set()
        dlg.focus_set()

    # ==========================
    #        MÓDULOS
    # ==========================
    def show_dashboard(self):
        self.clear_main_container()
        self.home_dashboard_manager = HomeDashboardManager(self.main_container, self)
        self._set_active_nav("home")

    def show_indicadores(self):
        self.clear_main_container()
        self.dashboard_manager = DashboardManager(self.main_container)
        self._set_active_nav("indicators")

    def show_inventory(self):
        # Seguridad extra por si intenta forzar desde código
        if not self._user_can_manage_inventory():
            messagebox.showerror(
                "Acceso denegado",
                "No tienes permisos para usar el módulo de inventario."
            )
            return

        self.clear_main_container()
        self.inventory_manager = InventoryManager(self.main_container, app=self)
        self._set_active_nav("inventory")

    def show_kardex(self):
        if not self._user_can_manage_inventory():
            messagebox.showerror(
                "Acceso denegado",
                "No tienes permisos para usar el kardex de inventario.",
            )
            return

        from image_manager import ImageManager
        from kardex_manager import KardexPanel

        self.clear_main_container()
        wrap = ctk.CTkFrame(self.main_container, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        hdr = ctk.CTkFrame(wrap, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            hdr,
            text="Kardex de inventario",
            font=("Arial", 14, "bold"),
            text_color="white",
        ).pack(side="left", padx=4)

        KardexPanel(
            wrap,
            self.db,
            ImageManager(),
            current_user=self.current_user,
            on_refresh_products=None,
        ).pack(fill="both", expand=True)
        self._set_active_nav("kardex")

    def show_compras(self):
        from operaciones_pos import ComprasFrame

        self.clear_main_container()
        ComprasFrame(self.main_container, current_user=self.current_user).pack(
            fill="both", expand=True, padx=8, pady=8
        )
        self._set_active_nav("compras")

    def show_devoluciones(self):
        from devoluciones_facturacion import DevolucionesFacturacionFrame

        self.clear_main_container()
        DevolucionesFacturacionFrame(
            self.main_container, current_user=self.current_user
        ).pack(fill="both", expand=True, padx=8, pady=8)
        self._set_active_nav("devoluciones")

    def show_combos(self):
        from operaciones_pos import CombosFrame

        self.clear_main_container()
        CombosFrame(self.main_container).pack(fill="both", expand=True, padx=8, pady=8)
        self._set_active_nav("combos")

    def _abrir_sincronizacion(self):
        from sincronizar_inventario import abrir_dialogo

        self._set_active_nav("sync")
        abrir_dialogo(self)

    def show_libro(self):
        from operaciones_pos import LibroFrame

        self.clear_main_container()
        LibroFrame(self.main_container, current_user=self.current_user).pack(
            fill="both", expand=True, padx=8, pady=8
        )
        self._set_active_nav("libro")

    def show_promociones(self):
        from operaciones_pos import PromocionesFrame

        self.clear_main_container()
        PromocionesFrame(self.main_container).pack(fill="both", expand=True, padx=8, pady=8)
        self._set_active_nav("otros")

    def show_facturacion(self):
        self.clear_main_container()
        self.facturacion_erp_manager = FacturacionERPManager(
            self.main_container,
            app=self,
            current_user=self.current_user,
            current_role=self.current_role,
        )
        self._set_active_nav("facturacion")

    def show_reportes(self, default_tab="📊 Ventas"):
        self.clear_main_container()
        self.reportes_manager = MainReportesManager(self.main_container, default_tab=default_tab)
        self._set_active_nav("reportes")

    def show_caja(self):
        self.clear_main_container()
        self.caja_manager = CajaManager(
            self.main_container,
            current_user=self.current_user,
            on_caja_abierta=self.show_facturacion,
        )
        self._set_active_nav("caja")

    def show_usuarios(self):
        # Seguridad extra por si acaso
        if self.current_role != "admin":
            messagebox.showerror(
                "Acceso denegado",
                "Solo administradores pueden gestionar usuarios."
            )
            return

        self.clear_main_container()
        self.users_manager = UsersManager(
            self.main_container,
            current_user=self.current_user,
            current_role=self.current_role
        )
        self._set_active_nav("users")

    def show_historial_facturas(self):
        self.clear_main_container()
        self.historial_facturas_manager = HistorialFacturasManager(
            self.main_container,
            current_role=self.current_role
        )
        self._set_active_nav("historial")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = BarSystemApp()
    app.run()
