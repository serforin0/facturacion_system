#!/usr/bin/env python3
"""
Badoo Auto-Login & Auto-Swiper Bot (CLI Multi-Cuenta)
------------------------------------------------------
Script de automatización para Badoo utilizando Playwright.
Soporta inicio de sesión automático, perfiles aislados por cuenta en `.badoo_sessions/`,
auto-likes (swiping) con retardos aleatorios, y ciclo para múltiples cuentas.

Uso:
    python badoo_bot.py --email tu_correo@ejemplo.com --password tu_clave
    python badoo_bot.py --all-accounts
"""

import os
import sys
import time
import random
import argparse
import getpass
from pathlib import Path
from dotenv import load_dotenv

from accounts_manager import AccountsManager

# Cargar variables de entorno si existe archivo .env
load_dotenv()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("❌ Error: Playwright no está instalado.")
    print("Ejecuta: pip install playwright && playwright install chromium")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bot Multi-Cuenta de Auto-Login y Auto-Clicker para Badoo"
    )
    parser.add_argument(
        "--url",
        default="https://badoo.com/signin",
        help="URL de inicio de sesión de Badoo (default: https://badoo.com/signin)",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("BADOO_EMAIL"),
        help="Correo electrónico o usuario de Badoo",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("BADOO_PASSWORD"),
        help="Contraseña de Badoo",
    )
    parser.add_argument(
        "--all-accounts",
        action="store_true",
        help="Ejecutar la automatización secuencialmente para todas las cuentas registradas en cuentas.json",
    )
    parser.add_argument(
        "--import-file",
        help="Ruta de un archivo .txt/.csv/.json para importar masivamente una lista de correos",
    )
    parser.add_argument(
        "--max-likes",
        type=int,
        default=100,
        help="Cantidad máxima de likes a dar por sesión/cuenta (default: 100)",
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=1.5,
        help="Tiempo mínimo de espera en segundos entre likes (default: 1.5)",
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=4.0,
        help="Tiempo máximo de espera en segundos entre likes (default: 4.0)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecutar el navegador en segundo plano (headless)",
    )
    return parser.parse_args()


def handle_popups(page):
    """Cierra modales y ventanas emergentes comunes en Badoo."""
    popup_selectors = [
        'button:has-text("No thanks")', 'button:has-text("Ahora no")',
        'button:has-text("Not now")', 'button:has-text("Omitir")',
        'button:has-text("Skip")', 'div[aria-label="Close"]',
        'div[aria-label="Cerrar"]', '[data-qa="close-modal"]',
    ]
    for selector in popup_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=400):
                btn.click()
                time.sleep(0.4)
        except Exception:
            pass


def process_single_account(account, args):
    email = account["email"]
    password = account["password"]
    mgr = AccountsManager()
    user_data_path = Path(mgr.get_session_dir(email)).resolve()

    print("\n=======================================================")
    print(f"👤 Procesando Cuenta: {email}")
    print(f"📁 Perfil de sesión aislado: {user_data_path}")
    print("=======================================================")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_path),
            headless=args.headless,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )

        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"🌐 Navegando a {args.url}...")
            page.goto(args.url, wait_until="domcontentloaded")
            time.sleep(2)

            # Verificar si hay sesión activa
            if "encounters" in page.url or page.locator('.js-profile-header').is_visible(timeout=1500):
                print("✅ Sesión previamente guardada activa.")
            else:
                print("🔑 Iniciando autenticación con credenciales...")
                
                # Email
                for sel in ['input[name="email"]', 'input[type="email"]', 'input[data-qa="login-email"]']:
                    try:
                        field = page.locator(sel).first
                        if field.is_visible(timeout=1000):
                            field.fill(email)
                            time.sleep(0.5)
                            break
                    except Exception:
                        continue

                # Password
                for sel in ['input[name="password"]', 'input[type="password"]', 'input[data-qa="login-password"]']:
                    try:
                        field = page.locator(sel).first
                        if field.is_visible(timeout=1000):
                            field.fill(password)
                            time.sleep(0.5)
                            break
                    except Exception:
                        continue

                # Clic login
                for sel in ['button[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Iniciar sesión")', '[data-qa="login-button"]']:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=1000):
                            btn.click()
                            time.sleep(3)
                            break
                    except Exception:
                        continue

            # Ir a Encuentros
            if "encounters" not in page.url:
                page.goto("https://badoo.com/encounters", wait_until="domcontentloaded")
                time.sleep(3)

            # Swiper / Auto-clicker
            print(f"⚡ Iniciando Likes (Meta: {args.max_likes})...")
            likes_given = 0

            like_selectors = [
                '[data-qa="profile-card-action-like"]',
                'button[aria-label="Like"]', 'div[aria-label="Like"]',
                'button[aria-label="Me gusta"]', 'div[aria-label="Me gusta"]',
                '.js-profile-header-vote-yes'
            ]

            for i in range(1, args.max_likes + 1):
                handle_popups(page)

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
                    delay = round(random.uniform(args.min_delay, args.max_delay), 2)
                    print(f"  💚 [{likes_given}/{args.max_likes}] Like enviado! ({delay}s)")
                    time.sleep(delay)

                    if likes_given % 15 == 0 and likes_given < args.max_likes:
                        pause_t = round(random.uniform(4.0, 7.0), 2)
                        print(f"  ☕ Micro-pausa humanizada ({pause_t}s)...")
                        time.sleep(pause_t)
                else:
                    time.sleep(1.5)

            print(f"🎉 Cuenta {email} completada! Total Likes: {likes_given}")

        except Exception as e:
            print(f"❌ Error en cuenta {email}: {e}")
        finally:
            context.close()


def main():
    args = parse_args()
    mgr = AccountsManager()

    if args.import_file:
        try:
            count = mgr.import_from_file(args.import_file)
            print(f"📥 Se importaron {count} cuentas desde '{args.import_file}'.")
        except Exception as e:
            print(f"❌ Error al importar archivo: {e}")
            sys.exit(1)

    accounts_to_run = []

    if args.all_accounts:
        accounts_to_run = mgr.accounts
        if not accounts_to_run:
            print("⚠️ No hay cuentas guardadas en cuentas.json.")
            sys.exit(1)
    else:
        email = args.email
        password = args.password
        if not email:
            email = input("Correo electrónico de Badoo: ").strip()
        if not password:
            password = getpass.getpass("Contraseña de Badoo: ").strip()
        
        accounts_to_run = [{"email": email, "password": password}]

    print("=======================================================")
    print("      BADOO AUTO-LOGIN & AUTO-CLICKER (MULTI-CUENTA)   ")
    print("=======================================================")
    print(f"📋 Total de cuentas a procesar: {len(accounts_to_run)}")

    for acc in accounts_to_run:
        process_single_account(acc, args)


if __name__ == "__main__":
    main()
