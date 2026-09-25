#!/usr/bin/env python3
"""
Accounts Manager - Gestor e Importador de Cuentas para Badoo Bot
-----------------------------------------------------------------
Maneja el almacenamiento, carga, importación desde archivos (.txt, .csv, .json),
adición y eliminación de credenciales de usuario. Soporta archivos solo con correos.
"""

import json
import re
from pathlib import Path

ACCOUNTS_FILE = Path("cuentas.json")
SESSIONS_DIR = Path(".badoo_sessions")


def sanitize_folder_name(email: str) -> str:
    """Convierte un correo electrónico en un nombre de carpeta válido y seguro."""
    clean = re.sub(r'[^a-zA-Z0-9_-]', '_', email)
    return clean.lower()


def is_valid_email(email: str) -> bool:
    """Verifica sintaxis básica de correo electrónico."""
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email.strip()))


class AccountsManager:
    def __init__(self, filepath=ACCOUNTS_FILE):
        self.filepath = Path(filepath)
        self.accounts = []
        self.load_accounts()

    def load_accounts(self):
        """Carga las cuentas desde `cuentas.json`."""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.accounts = data
                    else:
                        self.accounts = []
            except Exception as e:
                print(f"⚠️ Error al leer {self.filepath}: {e}")
                self.accounts = []
        else:
            self.accounts = []
            self.save_accounts()

    def save_accounts(self):
        """Guarda la lista actual de cuentas en `cuentas.json`."""
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.accounts, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error al guardar cuentas en {self.filepath}: {e}")

    def get_emails(self):
        """Retorna la lista de correos registrados."""
        return [acc.get("email", "") for acc in self.accounts if acc.get("email")]

    def get_account_by_email(self, email):
        """Obtiene una cuenta por correo."""
        for acc in self.accounts:
            if acc.get("email", "").strip().lower() == email.strip().lower():
                return acc
        return None

    def add_or_update_account(self, email, password=""):
        """Añade una cuenta o actualiza clave si ya existe."""
        email = email.strip()
        password = password.strip() if password else ""
        if not email:
            return False

        existing = self.get_account_by_email(email)
        if existing:
            if password:
                existing["password"] = password
        else:
            self.accounts.append({"email": email, "password": password})
        
        self.save_accounts()
        return True

    def delete_account(self, email):
        """Elimina una cuenta por su correo."""
        email_clean = email.strip().lower()
        initial_len = len(self.accounts)
        self.accounts = [acc for acc in self.accounts if acc.get("email", "").strip().lower() != email_clean]
        if len(self.accounts) < initial_len:
            self.save_accounts()
            return True
        return False

    def import_from_file(self, file_path_str) -> int:
        """
        Importa cuentas masivamente desde un archivo (.txt, .csv o .json).
        Soporta líneas con formatos:
        - correo (solo el correo por línea)
        - correo:contraseña
        - correo,contraseña
        - correo;contraseña
        - JSON de lista de cuentas
        Retorna la cantidad de cuentas importadas/actualizadas con éxito.
        """
        file_path = Path(file_path_str)
        if not file_path.exists():
            raise FileNotFoundError(f"El archivo {file_path} no existe.")

        imported_count = 0

        # Intentar leer como JSON
        if file_path.suffix.lower() == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                email = item.get("email") or item.get("correo")
                                password = item.get("password") or item.get("clave") or item.get("contraseña") or ""
                                if email and is_valid_email(email):
                                    if self.add_or_update_account(email, password):
                                        imported_count += 1
                            elif isinstance(item, str) and is_valid_email(item):
                                if self.add_or_update_account(item, ""):
                                    imported_count += 1
                        return imported_count
            except Exception:
                pass

        # Leer archivo como texto (.txt, .csv, o fallback)
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue

                email = None
                password = ""

                # Buscar si la línea tiene separador (:, ;, ,, \t)
                has_separator = False
                for sep in [":", ",", ";", "\t"]:
                    if sep in line:
                        parts = line.split(sep, 1)
                        possible_email = parts[0].strip()
                        possible_password = parts[1].strip()

                        if is_valid_email(possible_email):
                            email = possible_email
                            password = possible_password
                            has_separator = True
                            break

                # Si no tiene separador, comprobar si la línea completa es un correo válido
                if not has_separator:
                    # Extraer correo si la línea contiene un email
                    match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', line)
                    if match:
                        email = match.group(0).strip()
                        password = ""

                if email:
                    if self.add_or_update_account(email, password):
                        imported_count += 1

        return imported_count

    def get_session_dir(self, email):
        """Retorna el directorio de sesión para una cuenta."""
        folder_name = sanitize_folder_name(email)
        session_path = SESSIONS_DIR / folder_name
        session_path.mkdir(parents=True, exist_ok=True)
        return str(session_path)


if __name__ == "__main__":
    mgr = AccountsManager()
    print("Cuentas cargadas:", mgr.get_emails())
