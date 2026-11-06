import contextlib
import io

import pytest

from database import Database


@pytest.fixture
def base(tmp_path):
    """Base SQLite vacía de negocio, aislada del archivo real del proyecto."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return Database(str(tmp_path / "prueba.db"))


def insertar_producto(cur, codigo, nombre, stock=10, precio=100, bodega="Principal"):
    cur.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES ('Prueba')")
    categoria = cur.execute("SELECT id FROM categorias WHERE nombre='Prueba'").fetchone()[0]
    cur.execute(
        """
        INSERT INTO productos (
            nombre, precio, precio_base, precio_minimo, stock, categoria_id, stock_minimo,
            codigo_barras, activo, codigo_producto, unidad_medida, bodega_codigo,
            aplica_itbis, facturar_sin_stock
        ) VALUES (?, ?, ?, 0, ?, ?, 1, ?, 1, ?, 'BOT', ?, 1, 1)
        """,
        (nombre, precio, precio, stock, categoria, codigo, codigo, bodega),
    )
    return cur.execute("SELECT id FROM productos WHERE codigo_producto=?", (codigo,)).fetchone()[0]
