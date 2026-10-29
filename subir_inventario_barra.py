"""Agrega en el servidor los productos de la barra. No borra los que ya existen."""
import json
import os
import shutil
import sqlite3

RAIZ = "/opt/facturacion"
ORIGEN = "/tmp/barra_sync"


def categoria(cur, nombre: str):
    nombre = (nombre or "").strip() or "Otros"
    cur.execute("SELECT id FROM categorias WHERE nombre=?", (nombre,))
    fila = cur.fetchone()
    if fila:
        return int(fila[0])
    cur.execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
    return int(cur.lastrowid)


def main() -> None:
    os.chdir(RAIZ)
    with open(os.path.join(ORIGEN, "productos.json"), encoding="utf-8") as archivo:
        productos = json.load(archivo)
    conn = sqlite3.connect("bar_inventory.db")
    cur = conn.cursor()
    columnas = {fila[1] for fila in cur.execute("PRAGMA table_info(productos)")}
    nuevos = 0
    actualizados = 0
    for item in productos:
        codigo = str(item["codigo"]).strip()
        if not codigo:
            continue
        cur.execute(
            """
            SELECT id FROM productos
            WHERE TRIM(IFNULL(codigo_producto,'')) = ?
               OR TRIM(IFNULL(codigo_barras,'')) = ?
            ORDER BY id LIMIT 1
            """,
            (codigo, codigo),
        )
        existe = cur.fetchone()
        cat_id = categoria(cur, item.get("categoria") or "")
        imagen = ""
        archivo = item.get("imagen") or ""
        if archivo:
            destino = os.path.join(RAIZ, "web_static", "barra", os.path.basename(archivo))
            fuente = os.path.join(ORIGEN, "fotos", os.path.basename(archivo))
            if os.path.isfile(fuente):
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                shutil.copyfile(fuente, destino)
                imagen = "web_static/barra/" + os.path.basename(archivo)
        bodega = (item.get("bodega") or "BARRA").strip() or "BARRA"
        try:
            cur.execute("INSERT OR IGNORE INTO bodegas (codigo) VALUES (?)", (bodega,))
        except sqlite3.OperationalError:
            pass
        stock = float(item.get("stock") or 0)
        costo = float(item.get("precio_base") or 0)
        if existe:
            pid = int(existe[0])
            cur.execute(
                """
                UPDATE productos
                SET nombre=?, precio_base=?, stock=?, categoria_id=?,
                    unidad_medida=?, bodega_codigo=?,
                    imagen_path=CASE WHEN ? != '' THEN ? ELSE imagen_path END
                WHERE id=?
                """,
                (
                    item["nombre"], costo, stock, cat_id,
                    item.get("unidad") or "", bodega,
                    imagen, imagen, pid,
                ),
            )
            actualizados += 1
        else:
            campos = [
                "nombre", "descripcion", "precio", "precio_base", "precio_minimo", "stock",
                "categoria_id", "stock_minimo", "codigo_barras", "activo", "codigo_producto",
                "unidad_medida", "bodega_codigo", "aplica_itbis", "facturar_sin_stock",
            ]
            valores = [
                item["nombre"],
                "Conteo de barra. Precio inicial de mercado, sin precio de venta.",
                0, costo, 0, stock, cat_id, 1, codigo, 1, codigo,
                item.get("unidad") or "", bodega, 1, 1,
            ]
            if imagen and "imagen_path" in columnas:
                campos.append("imagen_path")
                valores.append(imagen)
            marcas = ",".join("?" for _ in campos)
            cur.execute(
                f"INSERT INTO productos ({', '.join(campos)}) VALUES ({marcas})",
                valores,
            )
            pid = int(cur.lastrowid)
            nuevos += 1
        cur.execute(
            "SELECT 1 FROM stock_bodega WHERE producto_id=? AND bodega=?",
            (pid, bodega),
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE stock_bodega SET cantidad=? WHERE producto_id=? AND bodega=?",
                (stock, pid, bodega),
            )
        else:
            cur.execute(
                "INSERT INTO stock_bodega (producto_id, bodega, cantidad) VALUES (?, ?, ?)",
                (pid, bodega, stock),
            )
    conn.commit()
    total = cur.execute(
        "SELECT COUNT(*) FROM productos WHERE bodega_codigo='BARRA'"
    ).fetchone()[0]
    conn.close()
    print(f"nuevos {nuevos} actualizados {actualizados} barra {total}")


if __name__ == "__main__":
    main()
