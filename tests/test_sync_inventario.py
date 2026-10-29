import contextlib
import io
import os
from datetime import datetime, timedelta

from database import Database
from sincronizar_inventario import diferencias, incorporar, paquete, responder


def _base(ruta):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return Database(ruta)


def _producto(cur, codigo, nombre, stock, bodega="BARRA"):
    cur.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES ('Prueba')")
    categoria = cur.execute("SELECT id FROM categorias WHERE nombre='Prueba'").fetchone()[0]
    cur.execute(
        """
        INSERT INTO productos (
            nombre, precio, precio_base, precio_minimo, stock, categoria_id, stock_minimo,
            codigo_barras, activo, codigo_producto, unidad_medida, bodega_codigo,
            aplica_itbis, facturar_sin_stock
        ) VALUES (?, 0, 100, 0, ?, ?, 1, ?, 1, ?, 'BOT', ?, 1, 1)
        """,
        (nombre, stock, categoria, codigo, codigo, bodega),
    )


def _venta(cur, numero, codigo, cantidad, fecha):
    cur.execute(
        """
        INSERT INTO facturas (
            numero, fecha, tipo_comprobante, subtotal, descuento_total, impuesto_total,
            total, estado, usuario
        ) VALUES (?, ?, 'consumidor_final', 10, 0, 0, 10, 'emitida', 'caja')
        """,
        (numero, fecha),
    )
    factura = cur.execute("SELECT last_insert_rowid()").fetchone()[0]
    producto = cur.execute("SELECT id FROM productos WHERE codigo_producto=?", (codigo,)).fetchone()[0]
    cur.execute(
        """
        INSERT INTO factura_detalle (
            factura_id, producto_id, descripcion, cantidad, precio_unitario,
            descuento_item, impuesto_item, total_linea
        ) VALUES (?, ?, 'Producto', ?, 10, 0, 0, 10)
        """,
        (factura, producto, cantidad),
    )


def _stock(cur, codigo):
    return float(cur.execute("SELECT stock FROM productos WHERE codigo_producto=?", (codigo,)).fetchone()[0])


def test_agrega_sin_borrar_y_no_descuenta_el_historial(tmp_path):
    web = _base(str(tmp_path / "web.db"))
    esc = _base(str(tmp_path / "esc.db"))
    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    cw = web.get_connection()
    ce = esc.get_connection()
    _producto(cw, "WEB-A", "Ron web", 20)
    _venta(cw, "F-WEB-1", "WEB-A", 5, ayer)
    _producto(ce, "ESC-B", "Agua escritorio", 8)
    cw.commit()
    ce.commit()

    ida = paquete(ce, str(tmp_path), False)
    respuesta = responder(cw, str(tmp_path), {
        "productos": ida["productos"],
        "ventas": ida["ventas"],
        "codigos": [p["codigo"] for p in ida["productos"]],
        "numeros": [v["numero"] for v in ida["ventas"]],
    }, "admin")
    incorporar(ce, str(tmp_path), respuesta["productos"], respuesta["ventas"], usuario="admin")

    assert _stock(cw, "ESC-B") == 8
    assert _stock(ce, "WEB-A") == 20
    assert _stock(cw, "WEB-A") == 20
    cw.execute("SELECT COUNT(*) FROM facturas WHERE numero='F-WEB-1'")
    assert cw.execute("SELECT COUNT(*) FROM facturas WHERE numero='F-WEB-1'").fetchone()[0] == 1
    assert ce.execute("SELECT COUNT(*) FROM facturas WHERE numero='F-WEB-1'").fetchone()[0] == 1

    cw.execute("SELECT valor FROM sync_estado WHERE clave='corte'")
    corte = cw.execute("SELECT valor FROM sync_estado WHERE clave='corte'").fetchone()[0]
    despues = (datetime.strptime(corte, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    _venta(cw, "F-WEB-2", "WEB-A", 3, despues)
    cw.execute("UPDATE productos SET stock = IFNULL(stock,0) - 3 WHERE codigo_producto='WEB-A'")
    cw.commit()

    ida = paquete(ce, str(tmp_path), False)
    respuesta = responder(cw, str(tmp_path), {
        "productos": ida["productos"],
        "ventas": ida["ventas"],
        "codigos": [p["codigo"] for p in ida["productos"]],
        "numeros": [v["numero"] for v in ida["ventas"]],
    }, "admin")
    incorporar(ce, str(tmp_path), respuesta["productos"], respuesta["ventas"], usuario="admin")
    assert _stock(cw, "WEB-A") == 17
    assert _stock(ce, "WEB-A") == 17

    ce.execute("UPDATE productos SET stock=1 WHERE codigo_producto='ESC-B'")
    ce.commit()
    local = {p["codigo"]: p["stock"] for p in paquete(ce, str(tmp_path), False)["productos"]}
    remoto = {p["codigo"]: p["stock"] for p in paquete(cw, str(tmp_path), False)["productos"]}
    nombres = {"ESC-B": "Agua escritorio"}
    cruce = diferencias(local, remoto, nombres)
    assert len(cruce) == 1
    assert cruce[0]["escritorio"] == 1
    assert cruce[0]["web"] == 8

    incorporar(cw, str(tmp_path), [], [], [{"codigo": "ESC-B", "stock": 5}], "admin")
    incorporar(ce, str(tmp_path), [], [], [{"codigo": "ESC-B", "stock": 5}], "admin")
    assert _stock(cw, "ESC-B") == 5
    assert _stock(ce, "ESC-B") == 5
    cw.close()
    ce.close()


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    carpeta = Path(tempfile.mkdtemp())
    test_agrega_sin_borrar_y_no_descuenta_el_historial(carpeta)
    print("sync ok")
