import customtkinter as ctk
import tkinterdnd2 as tkdnd   # lo dejamos importado por si lo usas en otros módulos
from tkinter import messagebox
from PIL import Image  # Para cargar el logo

from inventory_manager import InventoryManager
from styles import Styles
from database import Database
from factura_manager import FacturaManager              # Módulo de facturación
from main_reportes_manager import MainReportesManager   # Módulo general de reportes (Ventas + Inventario)
from caja_manager import CajaManager                    # Módulo de caja
from users_manager import UsersManager                  # 🔹 Módulo de gestión de usuarios
from historial_facturas_manager import HistorialFacturasManager


class BarSystemApp:
    def __init__(self):
        # Configurar tema PRIMERO
        Styles.setup_theme()

        # Inicializar base de datos (crea tablas y usuarios por defecto)
        self.db = Database()

        # ✅ Ventana principal: SIEMPRE CTk
        self.root = ctk.CTk()
        self.root.title("🍹 Esquina Tropical - Sistema de Bar")
        self.root.geometry("1200x700")
        self.root.minsize(1000, 600)

        # Fondo oscuro
        self.root.configure(fg_color='#2B2B2B')

        # Variables de sesión
        self.current_user = None
        self.current_role = None  # p.ej: "admin", "cajero"

        # Referencias a frames/módulos
        self.login_frame = None
        self.main_bg = None
        self.main_container = None
        self.inventory_manager = None
        self.factura_manager = None
        self.reportes_manager = None
        self.caja_manager = None
        self.users_manager = None
        self.historial_facturas_manager = None

        # Referencia al botón de inventario (para habilitar/deshabilitar)
        self.inventario_button = None

        # Mostrar primero el login
        self.show_login()

    # ==========================
    #       PANTALLA LOGIN
    # ==========================
    def show_login(self):
        # Limpiar todo por si acaso
        for widget in self.root.winfo_children():
            widget.destroy()

        self.login_frame = ctk.CTkFrame(self.root, fg_color="#2B2B2B")
        self.login_frame.pack(fill="both", expand=True)

        # ======= LOGO =======
        try:
            logo_path = "assets/logo.png"  # Ruta del logo
            logo_img = ctk.CTkImage(
                light_image=Image.open(logo_path),
                dark_image=Image.open(logo_path),
                size=(220, 220)
            )
            logo_label = ctk.CTkLabel(self.login_frame, image=logo_img, text="")
            logo_label.image = logo_img  # mantener referencia
            logo_label.pack(pady=(40, 10))
        except Exception as e:
            print("Error cargando logo:", e)

        # ======= TÍTULO =======
        title_label = ctk.CTkLabel(
            self.login_frame,
            text="Iniciar sesión",
            font=("Arial", 26, "bold"),
            text_color="white"
        )
        title_label.pack(pady=(5, 20))

        # ======= CAMPO USUARIO =======
        user_frame = ctk.CTkFrame(self.login_frame, fg_color="#2B2B2B")
        user_frame.pack(pady=10)
        user_label = ctk.CTkLabel(user_frame, text="Usuario:", font=("Arial", 15))
        user_label.grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.entry_user = ctk.CTkEntry(user_frame, width=260)
        self.entry_user.grid(row=0, column=1, padx=10, pady=5)

        # ======= CAMPO CONTRASEÑA =======
        pass_frame = ctk.CTkFrame(self.login_frame, fg_color="#2B2B2B")
        pass_frame.pack(pady=10)
        pass_label = ctk.CTkLabel(pass_frame, text="Contraseña:", font=("Arial", 15))
        pass_label.grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.entry_pass = ctk.CTkEntry(pass_frame, width=260, show="*")
        self.entry_pass.grid(row=0, column=1, padx=10, pady=5)

        # ======= BOTÓN LOGIN =======
        login_button = ctk.CTkButton(
            self.login_frame,
            text="Entrar",
            width=200,
            height=40,
            fg_color="#2B5F87",
            hover_color="#1D4C6B",
            command=self.handle_login
        )
        login_button.pack(pady=(25, 10))

        # Permitir Enter SOLO mientras estamos en login
        self.root.bind("<Return>", self._on_enter_login)

    def _on_enter_login(self, event):
        self.handle_login()

    def handle_login(self):
        username = self.entry_user.get().strip()
        password = self.entry_pass.get().strip()

        if not username or not password:
            messagebox.showwarning("Aviso", "Ingresa usuario y contraseña")
            return

        role = self.db.validate_user(username, password)

        if role is None:
            messagebox.showerror("Error", "Usuario o contraseña incorrectos")
            return

        # Login correcto
        self.current_user = username
        self.current_role = role  # "admin", "cajero", etc.

        # Dejar de escuchar Enter para login
        self.root.unbind("<Return>")

        # Destruir login y mostrar el sistema
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
    def setup_ui(self):
        # Frame de fondo principal
        self.main_bg = ctk.CTkFrame(self.root, fg_color='#2B2B2B')
        self.main_bg.pack(fill="both", expand=True)

        # Barra de título
        title_text = "🍹 ESQUINA TROPICAL - SISTEMA DE GESTIÓN"
        if self.current_user and self.current_role:
            title_text += f"   |   Usuario: {self.current_user} ({self.current_role})"

        title_label = ctk.CTkLabel(
            self.main_bg,
            text=title_text,
            font=("Arial", 24, "bold"),
            height=60,
            fg_color="#2B5F87",
            text_color="white"
        )
        title_label.pack(fill="x", padx=10, pady=10)

        # Barra de botones de módulos
        buttons_frame = ctk.CTkFrame(self.main_bg, fg_color="#2B2B2B")
        buttons_frame.pack(fill="x", padx=10, pady=(0, 10))

        # ====== INVENTARIO (solo admin) ======
        if self._user_can_manage_inventory():
            self.inventario_button = ctk.CTkButton(
                buttons_frame,
                text="📦 Inventario",
                width=150,
                height=40,
                command=self.show_inventory
            )
        else:
            # Botón deshabilitado y sin comando
            self.inventario_button = ctk.CTkButton(
                buttons_frame,
                text="📦 Inventario (solo admin)",
                width=150,
                height=40,
                state="disabled",
                fg_color="#555555",
                hover_color="#555555"
            )
        self.inventario_button.pack(side="left", padx=5, pady=5)

        # ====== FACTURA ======
        factura_button = ctk.CTkButton(
            buttons_frame,
            text="🧾 Factura",
            width=150,
            height=40,
            command=self.show_facturacion
        )
        factura_button.pack(side="left", padx=5, pady=5)

        # ====== REPORTES ======
        reportes_button = ctk.CTkButton(
            buttons_frame,
            text="📊 Reportes",
            width=150,
            height=40,
            command=self.show_reportes
        )
        reportes_button.pack(side="left", padx=5, pady=5)

        # ====== CAJA ======
        caja_button = ctk.CTkButton(
            buttons_frame,
            text="💰 Caja",
            width=150,
            height=40,
            command=self.show_caja
        )
        caja_button.pack(side="left", padx=5, pady=5)

        # ====== CONFIG IMPRESORA (TODOS PUEDEN VER) ======
        printer_button = ctk.CTkButton(
            buttons_frame,
            text="🖨️ Impresora",
            width=150,
            height=40,
            command=self.show_printer_config
        )
        printer_button.pack(side="left", padx=5, pady=5)

        # 🔹 SOLO SI ES ADMIN: botón para gestionar usuarios
        if self.current_role == "admin":
            usuarios_button = ctk.CTkButton(
                buttons_frame,
                text="👤 Usuarios",
                width=150,
                height=40,
                command=self.show_usuarios
            )
            usuarios_button.pack(side="left", padx=5, pady=5)

        # Historial de facturas
        facturas_button = ctk.CTkButton(
            buttons_frame,
            text="📜 Facturas",
            width=150,
            height=40,
            command=self.show_historial_facturas
        )
        facturas_button.pack(side="left", padx=5, pady=5)

        # Frame principal para módulos
        self.main_container = ctk.CTkFrame(self.main_bg, fg_color='#2B2B2B')
        self.main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Contenido inicial (mensaje de bienvenida)
        welcome_label = ctk.CTkLabel(
            self.main_container,
            text="Selecciona un módulo: Inventario, Factura, Reportes, Caja o Usuarios",
            font=("Arial", 18),
            text_color="white"
        )
        welcome_label.pack(expand=True)

    def clear_main_container(self):
        if self.main_container is not None:
            for widget in self.main_container.winfo_children():
                widget.destroy()
        self.inventory_manager = None
        self.factura_manager = None
        self.reportes_manager = None
        self.caja_manager = None
        self.users_manager = None
        self.historial_facturas_manager = None

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
    def show_inventory(self):
        # Seguridad extra por si intenta forzar desde código
        if not self._user_can_manage_inventory():
            messagebox.showerror(
                "Acceso denegado",
                "No tienes permisos para usar el módulo de inventario."
            )
            return

        self.clear_main_container()
        self.inventory_manager = InventoryManager(self.main_container)

    def show_facturacion(self):
        self.clear_main_container()
        self.factura_manager = FacturaManager(
            self.main_container,
            current_user=self.current_user
        )

    def show_reportes(self):
        self.clear_main_container()
        self.reportes_manager = MainReportesManager(self.main_container)

    def show_caja(self):
        self.clear_main_container()
        # 👉 Al abrir caja, lo manda automáticamente a facturación
        self.caja_manager = CajaManager(
            self.main_container,
            current_user=self.current_user,
            on_caja_abierta=self.show_facturacion
        )

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

    def show_historial_facturas(self):
        self.clear_main_container()
        self.historial_facturas_manager = HistorialFacturasManager(
            self.main_container,
            current_role=self.current_role
        )

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = BarSystemApp()
    app.run()
