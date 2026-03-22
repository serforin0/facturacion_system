#!/usr/bin/env python3
"""
Carga datos de demostración en bar_inventory.db solo si no hay productos.
Uso: python cargar_datos_demo.py

Si ya tiene catálogo, borre productos desde la app o use una copia nueva de la base.
"""

import os
import sqlite3

from demo_seed import apply_demo_seed_if_empty


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "bar_inventory.db")
    if not os.path.isfile(path):
        print(f"No existe {path}. Inicie la aplicación una vez para crear la base.")
        return
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    if apply_demo_seed_if_empty(cur):
        conn.commit()
        print("Listo. Reinicie la aplicación si estaba abierta.")
    else:
        print(
            "No se insertó nada: la tabla productos ya tiene filas.\n"
            "Para una base solo-demo, elimine bar_inventory.db y vuelva a abrir el sistema."
        )
    conn.close()


if __name__ == "__main__":
    main()
