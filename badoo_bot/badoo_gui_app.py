#!/usr/bin/env python3
"""
Badoo Auto-Login & Auto-Clicker Pro - Aplicación Multi-Cuenta con Importador
-----------------------------------------------------------------------------
Programa de escritorio profesional desarrollado con CustomTkinter y Playwright.
Permite importar listas de cuentas (.txt, .csv, .json), seleccionar correos,
guardar sesiones aisladas y ejecutar automatización individual o en secuencia.
"""

import os
import sys
import time
import random
import threading
from pathlib import Path
from tkinter import filedialog
from dotenv import load_dotenv

import customtkinter as ctk

from accounts_manager import AccountsManager

# Cargar variables de entorno si existen
load_dotenv()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("❌ Playwright no está instalado. Ejecuta: pip install playwright && playwright install chromium")


# Configurar apariencia de CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class BadooMultiBotThread(threading.Thread):
    """Hilo secundario que ejecuta la automatización para una o varias cuentas."""

    def __init__(self, accounts_to_run, global_config, log_callback, status_callback, on_finish_callback):
        super().__init__()
        self.accounts = accounts_to_run
        self.config = global_config
        self.log = log_callback
        self.update_status = status_callback
        self.on_finish = on_finish_callback
        self.stop_requested = False
        self.daemon = True

    def stop(self):
        self.stop_requested = True

    def run(self):
        total_accounts = len(self.accounts)
        self.log(f"🚀 Iniciando bot para {total_accounts} cuenta(s)...")

        for idx, acc in enumerate(self.accounts, 1):
            if self.stop_requested:
                break

            email = acc["email"]
            # Usar la contraseña individual si existe, sino la contraseña del campo global
            password = acc.get("password") or self.config.get("global_password") or ""
            url = self.config["url"]
            max_likes = self.config["max_likes"]
            min_delay = self.config["min_delay"]
            max_delay = self.config["max_delay"]
            headless = self.config["headless"]
            
            mgr = AccountsManager()
            user_data_dir = mgr.get_session_dir(email)

            self.log(f"\n=======================================================")
            self.log(f"👤 [{idx}/{total_accounts}] Procesando Cuenta: {email}")
            self.log(f"📁 Sesión aislada: {user_data_dir}")
            self.log(f"=======================================================")
            self.update_status(f"Cuenta {idx}/{total_accounts} ({email})", 0.05)

            try:
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        headless=headless,
                        viewport={"width": 1280, "height": 800},
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                    )

                    page = context.pages[0] if context.pages else context.new_page()

                    if self.stop_requested:
                        context.close()
                        break

                    # 1. LOGIN
                    self.log(f"🌐 Navegando a {url}...")
                    page.goto(url, wait_until="domcontentloaded")
                    time.sleep(2)

                    current_url = page.url
                    if "encounters" in current_url or page.locator('.js-profile-header').is_visible(timeout=1500):
                        self.log("✅ Sesión previamente guardada activa.")
                    else:
                        self.log("🔑 Autenticando con credenciales...")

                        for sel in ['input[name="email"]', 'input[type="email"]', 'input[data-qa="login-email"]']:
                            try:
                                field = page.locator(sel).first
                                if field.is_visible(timeout=1000):
                                    field.fill(email)
                                    time.sleep(0.5)
                                    break
                            except Exception:
                                continue

                        for sel in ['input[name="password"]', 'input[type="password"]', 'input[data-qa="login-password"]']:
                            try:
                                field = page.locator(sel).first
                                if field.is_visible(timeout=1000):
                                    field.fill(password)
                                    time.sleep(0.5)
                                    break
                            except Exception:
                                continue

                        for sel in ['button[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Iniciar sesión")', '[data-qa="login-button"]']:
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=1000):
                                    btn.click()
                                    time.sleep(3)
                                    break
                            except Exception:
                                continue

                    if self.stop_requested:
                        context.close()
                        break

                    if "encounters" not in page.url:
                        self.log("🌐 Navegando a Encuentros...")
                        page.goto("https://badoo.com/encounters", wait_until="domcontentloaded")
                        time.sleep(3)

                    # 2. AUTO-CLICKER
                    self.log(f"⚡ Dando hasta {max_likes} Likes...")
                    likes_given = 0

                    like_selectors = [
                        '[data-qa="profile-card-action-like"]',
                        'button[aria-label="Like"]', 'div[aria-label="Like"]',
                        'button[aria-label="Me gusta"]', 'div[aria-label="Me gusta"]',
                        '.js-profile-header-vote-yes'
                    ]

                    popup_selectors = [
                        'button:has-text("No thanks")', 'button:has-text("Ahora no")',
                        'button:has-text("Not now")', 'button:has-text("Omitir")',
                        'button:has-text("Skip")', 'div[aria-label="Close"]',
                        'div[aria-label="Cerrar"]', '[data-qa="close-modal"]'
                    ]

                    while likes_given < max_likes and not self.stop_requested:
                        for sel in popup_selectors:
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=400):
                                    btn.click()
                                    time.sleep(0.4)
                            except Exception:
                                pass

                        if self.stop_requested:
                            break

                        clicked = False
                        for sel in like_selectors:
                            try:
                                btn = page.locator(sel).first
                                if btn.is_visible(timeout=400):
                                    btn.click()
                                    clicked = True
                                    break
                            except Exception:
                                continue

                        if not clicked:
                            try:
                                page.keyboard.press("1")
                                clicked = True
                            except Exception:
                                pass

                        if clicked:
                            likes_given += 1
                            progress = likes_given / max_likes
                            self.update_status(f"Cuenta {idx}/{total_accounts} [{likes_given}/{max_likes} Likes]", progress)
                            
                            delay = round(random.uniform(min_delay, max_delay), 2)
                            self.log(f"💚 [{likes_given}/{max_likes}] Like registrado! (Espera: {delay}s)")
                            
                            for _ in range(int(delay * 10)):
                                if self.stop_requested:
                                    break
                                time.sleep(0.1)

                            if likes_given % 15 == 0 and likes_given < max_likes:
                                pause_t = round(random.uniform(4.0, 7.0), 2)
                                self.log(f"☕ Micro-pausa técnica ({pause_t}s)...")
                                for _ in range(int(pause_t * 10)):
                                    if self.stop_requested:
                                        break
                                    time.sleep(0.1)
                        else:
                            self.log("⏳ Esperando perfil...")
                            time.sleep(1.5)

                    self.log(f"✅ Cuenta {email} completada ({likes_given} Likes).")
                    context.close()

            except Exception as e:
                self.log(f"❌ Error en cuenta {email}: {e}")

        if self.stop_requested:
            self.log("\n🛑 Proceso detenido por el usuario.")
            self.update_status("Detenido", 0.0)
        else:
            self.log("\n🎉 ¡Proceso finalizado para todas las cuentas!")
            self.update_status("Completado", 1.0)

        self.on_finish()


class BadooApp(ctk.CTk):
    """Interfaz Gráfica Principal con Importador de Archivos de Cuentas."""

    def __init__(self):
        super().__init__()

        self.title("Badoo Auto-Login & Auto-Clicker Pro")
        self.geometry("980 x 720")
        self.minsize(850, 650)

        self.accounts_mgr = AccountsManager()
        self.bot_thread = None

        self._create_layout()
        self._load_accounts_into_ui()

    def _create_layout(self):
        self.grid_columnconfigure(0, weight=4)
        self.grid_columnconfigure(1, weight=5)
        self.grid_rowconfigure(0, weight=1)

        # PANEL IZQUIERDO
        left_frame = ctk.CTkFrame(self, corner_radius=12)
        left_frame.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")

        ctk.CTkLabel(
            left_frame, text="⚡ Badoo Multi-Cuenta Bot", font=ctk.CTkFont(size=20, weight="bold")
        ).pack(padx=15, pady=(15, 10), anchor="w")

        # GESTOR MULTI-CUENTA
        acc_box = ctk.CTkFrame(left_frame, fg_color="gray17", corner_radius=8)
        acc_box.pack(padx=15, pady=5, fill="x")

        # Fila superior del caja de cuentas: Título y Botón Importar Archivo
        acc_header = ctk.CTkFrame(acc_box, fg_color="transparent")
        acc_header.pack(padx=10, pady=(8, 2), fill="x")

        ctk.CTkLabel(acc_header, text="👤 Seleccionar Cuenta:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        
        self.btn_import_file = ctk.CTkButton(
            acc_header, text="📁 Cargar Archivo", font=ctk.CTkFont(size=11), fg_color="#8e44ad", hover_color="#9b59b6", width=110, command=self.import_accounts_file
        )
        self.btn_import_file.pack(side="right")

        self.option_accounts = ctk.CTkOptionMenu(
            acc_box, values=["(Sin cuentas registradas)"], command=self._on_account_selected
        )
        self.option_accounts.pack(padx=10, pady=(4, 8), fill="x")

        # Botones Cuentas
        acc_btn_frame = ctk.CTkFrame(acc_box, fg_color="transparent")
        acc_btn_frame.pack(padx=10, pady=(0, 8), fill="x")

        self.btn_add_acc = ctk.CTkButton(
            acc_btn_frame, text="💾 Guardar Cuenta", font=ctk.CTkFont(size=11), fg_color="#2980b9", hover_color="#3498db", command=self.save_current_account
        )
        self.btn_add_acc.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_del_acc = ctk.CTkButton(
            acc_btn_frame, text="🗑️ Eliminar Cuenta", font=ctk.CTkFont(size=11), fg_color="#c0392b", hover_color="#e74c3c", command=self.delete_current_account
        )
        self.btn_del_acc.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # ENTRADAS
        ctk.CTkLabel(left_frame, text="Correo Electrónico / Usuario:", font=ctk.CTkFont(size=12)).pack(padx=15, pady=(8, 0), anchor="w")
        self.entry_email = ctk.CTkEntry(left_frame, placeholder_text="tu_correo@ejemplo.com")
        self.entry_email.pack(padx=15, pady=(0, 8), fill="x")

        ctk.CTkLabel(left_frame, text="Contraseña:", font=ctk.CTkFont(size=12)).pack(padx=15, pady=(4, 0), anchor="w")
        self.entry_password = ctk.CTkEntry(left_frame, placeholder_text="••••••••", show="*")
        self.entry_password.pack(padx=15, pady=(0, 8), fill="x")

        # URL
        ctk.CTkLabel(left_frame, text="URL de Inicio de Sesión:", font=ctk.CTkFont(size=12)).pack(padx=15, pady=(4, 0), anchor="w")
        self.entry_url = ctk.CTkEntry(left_frame, placeholder_text="https://badoo.com/signin")
        self.entry_url.insert(0, "https://badoo.com/signin")
        self.entry_url.pack(padx=15, pady=(0, 8), fill="x")

        # PARÁMETROS
        self.likes_label = ctk.CTkLabel(left_frame, text="Límite de Likes: 100", font=ctk.CTkFont(size=12, weight="bold"))
        self.likes_label.pack(padx=15, pady=(4, 0), anchor="w")
        self.slider_likes = ctk.CTkSlider(
            left_frame, from_=10, to=500, number_of_steps=49, command=self._on_likes_slider_change
        )
        self.slider_likes.set(100)
        self.slider_likes.pack(padx=15, pady=(0, 8), fill="x")

        self.delay_label = ctk.CTkLabel(left_frame, text="Demora por clic: 1.5s - 4.0s", font=ctk.CTkFont(size=12))
        self.delay_label.pack(padx=15, pady=(4, 0), anchor="w")

        delay_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        delay_frame.pack(padx=15, pady=(0, 8), fill="x")

        self.slider_min_delay = ctk.CTkSlider(delay_frame, from_=0.5, to=5.0, number_of_steps=45, command=self._on_delay_slider_change)
        self.slider_min_delay.set(1.5)
        self.slider_min_delay.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.slider_max_delay = ctk.CTkSlider(delay_frame, from_=2.0, to=10.0, number_of_steps=80, command=self._on_delay_slider_change)
        self.slider_max_delay.set(4.0)
        self.slider_max_delay.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # OPCIONES DE EJECUCIÓN
        self.switch_all = ctk.CTkCheckBox(left_frame, text="🔁 Ejecutar TODAS las cuentas en secuencia", font=ctk.CTkFont(size=12, weight="bold"))
        self.switch_all.pack(padx=15, pady=5, anchor="w")

        self.switch_headless = ctk.CTkSwitch(left_frame, text="Modo en segundo plano (Headless)")
        self.switch_headless.pack(padx=15, pady=5, anchor="w")

        # BOTONES INICIO Y DETENCIÓN
        btn_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        btn_frame.pack(padx=15, pady=(15, 10), fill="x")

        self.btn_start = ctk.CTkButton(
            btn_frame,
            text="▶ INICIAR BOT",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#27ae60",
            hover_color="#219653",
            height=40,
            command=self.start_bot,
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_stop = ctk.CTkButton(
            btn_frame,
            text="⏹ DETENER",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#e74c3c",
            hover_color="#c0392b",
            height=40,
            state="disabled",
            command=self.stop_bot,
        )
        self.btn_stop.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # PANEL DERECHO: CONSOLA
        right_frame = ctk.CTkFrame(self, corner_radius=12)
        right_frame.grid(row=0, column=1, padx=(0, 15), pady=15, sticky="nsew")

        right_frame.grid_rowconfigure(1, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            right_frame, text="Estado: Listo", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.status_label.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")

        self.progress_bar = ctk.CTkProgressBar(right_frame)
        self.progress_bar.set(0.0)
        self.progress_bar.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="ew")

        self.log_textbox = ctk.CTkTextbox(
            right_frame,
            font=ctk.CTkFont(family="Courier", size=12),
            corner_radius=8,
            wrap="word",
        )
        self.log_textbox.grid(row=1, column=0, padx=15, pady=(30, 15), sticky="nsew")

        self.log("ℹ️ Aplicación Multi-Cuenta lista.")
        self.log("💡 Puedes hacer clic en '📁 Cargar Archivo' para importar tu lista de correos (.txt/.csv).")

    # LOGICA DE ARCHIVOS Y CUENTAS
    def import_accounts_file(self):
        """Abre un selector nativo de archivos para importar correos y contraseñas."""
        file_path = filedialog.askopenfilename(
            title="Seleccionar archivo de lista de correos",
            filetypes=[
                ("Archivos de Cuentas", "*.txt *.csv *.json"),
                ("Archivos de Texto", "*.txt"),
                ("Archivos CSV", "*.csv"),
                ("Archivos JSON", "*.json"),
                ("Todos los archivos", "*.*"),
            ]
        )

        if not file_path:
            return

        try:
            count = self.accounts_mgr.import_from_file(file_path)
            file_name = Path(file_path).name
            if count > 0:
                self.log(f"📥 ¡Éxito! Se importaron {count} cuenta(s) desde '{file_name}'.")
                self._load_accounts_into_ui()
            else:
                self.log(f"⚠️ No se encontraron cuentas con formato válido en '{file_name}'.")
                self.log("💡 Formatos soportados por línea: correo:contraseña o correo,contraseña")
        except Exception as e:
            self.log(f"❌ Error al importar archivo: {e}")

    def _load_accounts_into_ui(self):
        emails = self.accounts_mgr.get_emails()
        if emails:
            self.option_accounts.configure(values=emails)
            self.option_accounts.set(emails[0])
            self._on_account_selected(emails[0])
        else:
            self.option_accounts.configure(values=["(Sin cuentas registradas)"])
            self.option_accounts.set("(Sin cuentas registradas)")

    def _on_account_selected(self, selected_email):
        acc = self.accounts_mgr.get_account_by_email(selected_email)
        if acc:
            self.entry_email.delete(0, "end")
            self.entry_email.insert(0, acc["email"])

            self.entry_password.delete(0, "end")
            self.entry_password.insert(0, acc["password"])

    def save_current_account(self):
        email = self.entry_email.get().strip()
        password = self.entry_password.get().strip()
        if not email or not password:
            self.log("⚠️ Ingresa un correo y contraseña para guardar.")
            return

        self.accounts_mgr.add_or_update_account(email, password)
        self.log(f"💾 Cuenta {email} guardada correctamente.")
        self._load_accounts_into_ui()
        self.option_accounts.set(email)

    def delete_current_account(self):
        email = self.entry_email.get().strip()
        if not email:
            return

        if self.accounts_mgr.delete_account(email):
            self.log(f"🗑️ Cuenta {email} eliminada.")
            self.entry_email.delete(0, "end")
            self.entry_password.delete(0, "end")
            self._load_accounts_into_ui()
        else:
            self.log(f"⚠️ La cuenta {email} no estaba registrada.")

    # EVENTOS SLIDERS Y LOGS
    def _on_likes_slider_change(self, value):
        self.likes_label.configure(text=f"Límite de Likes: {int(value)}")

    def _on_delay_slider_change(self, _=None):
        min_v = round(self.slider_min_delay.get(), 1)
        max_v = round(self.slider_max_delay.get(), 1)
        if min_v > max_v:
            max_v = min_v
            self.slider_max_delay.set(max_v)
        self.delay_label.configure(text=f"Demora por clic: {min_v}s - {max_v}s")

    def log(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.log_textbox.insert("end", f"[{timestamp}] {text}\n")
        self.log_textbox.see("end")

    def update_status(self, message, progress=None):
        self.status_label.configure(text=f"Estado: {message}")
        if progress is not None:
            self.progress_bar.set(progress)

    def start_bot(self):
        run_all = bool(self.switch_all.get())
        
        accounts_to_run = []
        if run_all:
            accounts_to_run = self.accounts_mgr.accounts
            if not accounts_to_run:
                email = self.entry_email.get().strip()
                password = self.entry_password.get().strip()
                if email and password:
                    accounts_to_run = [{"email": email, "password": password}]
        else:
            email = self.entry_email.get().strip()
            password = self.entry_password.get().strip()
            if email and password:
                accounts_to_run = [{"email": email, "password": password}]

        if not accounts_to_run:
            self.log("⚠️ No hay cuentas válidas. Carga un archivo de correos o ingresa datos.")
            return

        global_config = {
            "url": self.entry_url.get().strip() or "https://badoo.com/signin",
            "global_password": self.entry_password.get().strip(),
            "max_likes": int(self.slider_likes.get()),
            "min_delay": round(self.slider_min_delay.get(), 1),
            "max_delay": round(self.slider_max_delay.get(), 1),
            "headless": bool(self.switch_headless.get()),
        }

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.update_status("Iniciando automatización...", 0.0)

        self.bot_thread = BadooMultiBotThread(
            accounts_to_run=accounts_to_run,
            global_config=global_config,
            log_callback=self.log,
            status_callback=self.update_status,
            on_finish_callback=self._on_bot_finish,
        )
        self.bot_thread.start()

    def stop_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            self.log("⏳ Solicitando detención...")
            self.bot_thread.stop()
            self.btn_stop.configure(state="disabled")

    def _on_bot_finish(self):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")


if __name__ == "__main__":
    app = BadooApp()
    app.mainloop()
