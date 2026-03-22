"""
Rutas de datos cuando la app corre empaquetada (PyInstaller).
En modo .exe, la base de datos, tickets y configuración deben escribirse junto al ejecutable,
no en la carpeta temporal _MEI_*.
"""
import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_directory() -> str:
    """Directorio persistente: carpeta del .exe si está congelada; si no, del proyecto."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))
