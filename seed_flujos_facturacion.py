#!/usr/bin/env python3
"""
Datos de prueba para los flujos de facturación extendidos:
presupuesto (cotización), confirmar venta, cobro parcial, devolución/NC,
notas/referencia, PDF/correo (cliente con e-mail), importación en lote.

Uso:
  python3 seed_flujos_facturacion.py          # inserta / actualiza suite TEST-FLUJO-*
  python3 seed_flujos_facturacion.py --limpiar # solo elimina documentos TEST-FLUJO-*

Requisito: base inicializada (ej. bar_inventory.db tras abrir la app una vez).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from database import Database

PREFIX = "TEST-FLUJO-"
COD_PROD_A = "TST-FLW-01"
COD_PROD_B = "TST-FLW-02"
STOCK_BASE = 850


def _line_itbis(cant: float, precio: float, desc: float = 0.0, aplica: bool = True):
    sub = round(cant * precio - desc, 2)
    imp = round(sub * 0.18, 2) if aplica else 0.0
    total = round(sub + imp, 2)
    return sub, imp, total


def _limpiar_documentos_prueba(cur) -> int:
    """Elimina facturas y relaciones cuyo número empieza por PREFIX."""
    cur.execute(
        "SELECT id FROM facturas WHERE numero LIKE ?",
        (PREFIX + "%",),
    )
    ids = [int(r[0]) for r in cur.fetchall()]
    for fid in ids:
        cur.execute(
            """
            DELETE FROM notas_credito_detalle WHERE nota_credito_id IN (
                SELECT id FROM notas_credito WHERE factura_original_id = ?
            )
            """,
            (fid,),
        )
        cur.execute(
            "DELETE FROM notas_credito WHERE factura_original_id = ?",
            (fid,),
        )
        cur.execute("DELETE FROM pagos_factura WHERE factura_id = ?", (fid,))
        cur.execute("DELETE FROM factura_detalle WHERE factura_id = ?", (fid,))
        cur.execute("DELETE FROM movimientos_inventario WHERE factura_id = ?", (fid,))
    cur.execute("DELETE FROM facturas WHERE numero LIKE ?", (PREFIX + "%",))
    return len(ids)


def _categoria_otros_id(cur) -> int:
    cur.execute("SELECT id FROM categorias WHERE nombre = 'Otros' LIMIT 1")
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur.execute("INSERT INTO categorias (nombre) VALUES ('Otros')")
    return int(cur.lastrowid)


def _ensure_producto(
    cur,
    codigo: str,
    nombre: str,
    precio: float,
    *,
    aplica_itbis: bool = True,
) -> int:
    cur.execute(
        "SELECT id, nombre FROM productos WHERE TRIM(IFNULL(codigo_producto,'')) = ?",
        (codigo,),
    )
    row = cur.fetchone()
    cat = _categoria_otros_id(cur)
    if row:
        pid = int(row[0])
        cur.execute(
            """
            UPDATE productos
            SET nombre = ?, precio = ?, precio_base = ?, precio_minimo = ?,
                stock = ?, aplica_itbis = ?, activo = 1, facturar_sin_stock = 1,
                bodega_codigo = 'Principal'
            WHERE id = ?
            """,
            (
                nombre,
                precio,
                precio * 0.65,
                precio * 0.85,
                STOCK_BASE,
                1 if aplica_itbis else 0,
                pid,
            ),
        )
        return pid
    cur.execute(
        """
        INSERT INTO productos (
            nombre, descripcion, precio, precio_base, precio_minimo, stock, categoria_id,
            stock_minimo, codigo_barras, activo, codigo_producto, tipo_producto, unidad_medida,
            bodega_codigo, aplica_itbis, facturar_sin_stock, proveedor_id, precio_2
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)
        """,
        (
            nombre,
            "Ítem de prueba — flujos ERP",
            precio,
            round(precio * 0.65, 2),
            round(precio * 0.85, 2),
            STOCK_BASE,
            cat,
            5,
            "TSTCB" + codigo[-2:],
            1,
            codigo,
            "Físico",
            "Unidad",
            "Principal",
            1 if aplica_itbis else 0,
            1,
            round(precio * 0.97, 2),
        ),
    )
    return int(cur.lastrowid)


def _ensure_cliente(
    cur,
    nombre: str,
    documento: str,
    *,
    email: str | None = None,
    telefono: str = "809-555-0000",
) -> int:
    cur.execute(
        """
        SELECT id FROM clientes
        WHERE TRIM(IFNULL(documento,'')) = ?
        LIMIT 1
        """,
        (documento.strip(),),
    )
    row = cur.fetchone()
    if row:
        cid = int(row[0])
        cur.execute(
            """
            UPDATE clientes
            SET nombre = ?, telefono = ?, email = COALESCE(?, email)
            WHERE id = ?
            """,
            (nombre, telefono, email, cid),
        )
        return cid
    cur.execute(
        """
        INSERT INTO clientes (nombre, documento, tipo_documento, telefono, email, direccion)
        VALUES (?, ?, 'rnc', ?, ?, 'Zona de prueba')
        """,
        (nombre, documento.strip(), telefono, email),
    )
    return int(cur.lastrowid)


def _condicion_id(cur, codigo: str) -> int | None:
    cur.execute("SELECT id FROM condiciones_pago WHERE codigo = ?", (codigo,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _insert_factura_detalle(cur, factura_id: int, pid: int, descripcion: str, cant, pu, desc, im, tl):
    cur.execute(
        """
        INSERT INTO factura_detalle (
            factura_id, producto_id, descripcion, cantidad,
            precio_unitario, descuento_item, impuesto_item, total_linea
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (factura_id, pid, descripcion, cant, pu, desc, im, tl),
    )


def aplicar_seed(db: Database) -> None:
    conn = db.get_connection()
    cur = conn.cursor()
    n_borrados = _limpiar_documentos_prueba(cur)

    pid_a = _ensure_producto(
        cur, COD_PROD_A, "Producto prueba A — bebida", 120.0, aplica_itbis=True
    )
    pid_b = _ensure_producto(
        cur, COD_PROD_B, "Producto prueba B — snack", 85.0, aplica_itbis=True
    )

    id_mail = _ensure_cliente(
        cur,
        "Cliente Prueba (correo)",
        "40299887701",
        email="facturacion.demo@prueba.local",
        telefono="809-555-1001",
    )
    id_cred = _ensure_cliente(
        cur,
        "Cliente Prueba (crédito parcial)",
        "40299887702",
        email=None,
        telefono="809-555-1002",
    )
    id_lote = _ensure_cliente(
        cur,
        "Cliente Prueba (importación lote)",
        "88877766601",
        email="lote.import@prueba.local",
        telefono="809-555-1003",
    )

    id_cont = _condicion_id(cur, "CONT")
    id_c30 = _condicion_id(cur, "C30")
    fv_pres = (datetime.now().date() + timedelta(days=30)).isoformat()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # —— 1) Presupuesto: cotización con vencimiento y notas ——
    nro_pres = f"{PREFIX}PRES-001"
    sb1, im1, tl1 = _line_itbis(6, 120.0, 0.0, True)
    sb2, im2, tl2 = _line_itbis(4, 85.0, 0.0, True)
    subtotal = round(sb1 + sb2, 2)
    impuesto = round(im1 + im2, 2)
    total_pres = round(subtotal + impuesto, 2)
    cur.execute(
        """
        INSERT INTO facturas (
            numero, fecha, tipo_comprobante, cliente_id,
            subtotal, descuento_total, impuesto_total, total, estado, usuario, caja,
            condicion_pago_id, fecha_vencimiento, observaciones, referencia_entrega,
            moneda, tasa_cambio
        ) VALUES (?,?,?,?,?,?,?,?, 'cotizacion', ?, NULL, ?, ?, ?, ?, 'DOP', 1.0)
        """,
        (
            nro_pres,
            ahora,
            "consumidor_final",
            id_mail,
            subtotal,
            0.0,
            impuesto,
            total_pres,
            "admin",
            id_c30,
            fv_pres,
            "Presupuesto de prueba — no mueve inventario hasta confirmar venta.",
            "OC-PRUEBA-1001",
        ),
    )
    fid_pres = cur.lastrowid
    _insert_factura_detalle(
        cur, fid_pres, pid_a, "Producto prueba A — bebida", 6, 120.0, 0.0, im1, tl1
    )
    _insert_factura_detalle(
        cur, fid_pres, pid_b, "Producto prueba B — snack", 4, 85.0, 0.0, im2, tl2
    )

    # —— 2) Factura emitida saldada (devolución parcial posible) ——
    nro_vta = f"{PREFIX}VTA-001"
    q_a, q_b = 10.0, 8.0
    sb1, im1, tl1 = _line_itbis(q_a, 120.0, 0.0, True)
    sb2, im2, tl2 = _line_itbis(q_b, 85.0, 0.0, True)
    subtotal = round(sb1 + sb2, 2)
    impuesto = round(im1 + im2, 2)
    total_vta = round(subtotal + impuesto, 2)
    cur.execute(
        """
        INSERT INTO facturas (
            numero, fecha, tipo_comprobante, cliente_id,
            subtotal, descuento_total, impuesto_total, total, estado, usuario, caja,
            condicion_pago_id, fecha_vencimiento, observaciones, moneda, tasa_cambio
        ) VALUES (?,?,?,?,?,?,?,?, 'emitida', ?, NULL, ?, NULL, ?, 'DOP', 1.0)
        """,
        (
            nro_vta,
            ahora,
            "consumidor_final",
            id_mail,
            subtotal,
            0.0,
            impuesto,
            total_vta,
            "admin",
            id_cont,
            "Venta de prueba para devolución / nota de crédito.",
        ),
    )
    fid_vta = cur.lastrowid
    _insert_factura_detalle(
        cur, fid_vta, pid_a, "Producto prueba A — bebida", q_a, 120.0, 0.0, im1, tl1
    )
    _insert_factura_detalle(
        cur, fid_vta, pid_b, "Producto prueba B — snack", q_b, 85.0, 0.0, im2, tl2
    )
    cur.execute(
        """
        INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia)
        VALUES (?, 'efectivo', ?, NULL)
        """,
        (fid_vta, total_vta),
    )
    for pid, qty, pu, num, desc in (
        (pid_a, q_a, 120.0, nro_vta, "Venta prueba A"),
        (pid_b, q_b, 85.0, nro_vta, "Venta prueba B"),
    ):
        db.insert_movimiento_kardex(
            pid,
            "venta",
            -float(qty),
            ajustar_stock=True,
            referencia=num,
            factura_id=fid_vta,
            usuario="admin",
            tipo_codigo="FA",
            entidad_nombre="Cliente Prueba (correo)",
            bodega_codigo="Principal",
            precio_unitario=float(pu),
            descripcion_mov=f"Venta semilla: {num}",
            conn=conn,
        )

    # —— 3) Factura emitida con balance (cuentas por cobrar) ——
    nro_cred = f"{PREFIX}CRED-001"
    q_ca, q_cb = 3.0, 5.0
    sb1, im1, tl1 = _line_itbis(q_ca, 120.0, 0.0, True)
    sb2, im2, tl2 = _line_itbis(q_cb, 85.0, 0.0, True)
    subtotal = round(sb1 + sb2, 2)
    impuesto = round(im1 + im2, 2)
    total_cred = round(subtotal + impuesto, 2)
    pago_ini = round(total_cred * 0.35, 2)
    balance = round(total_cred - pago_ini, 2)
    cur.execute(
        """
        INSERT INTO facturas (
            numero, fecha, tipo_comprobante, cliente_id,
            subtotal, descuento_total, impuesto_total, total, estado, usuario, caja,
            condicion_pago_id, fecha_vencimiento, observaciones, moneda, tasa_cambio
        ) VALUES (?,?,?,?,?,?,?,?, 'emitida', ?, NULL, ?, NULL, ?, 'DOP', 1.0)
        """,
        (
            nro_cred,
            ahora,
            "credito_fiscal",
            id_cred,
            subtotal,
            0.0,
            impuesto,
            total_cred,
            "admin",
            id_c30,
            "Factura con saldo — use «Pagar documento» en el listado.",
        ),
    )
    fid_cred = cur.lastrowid
    _insert_factura_detalle(
        cur, fid_cred, pid_a, "Producto prueba A — bebida", q_ca, 120.0, 0.0, im1, tl1
    )
    _insert_factura_detalle(
        cur, fid_cred, pid_b, "Producto prueba B — snack", q_cb, 85.0, 0.0, im2, tl2
    )
    cur.execute(
        """
        INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia)
        VALUES (?, 'transferencia', ?, 'REF-PRUEBA-01')
        """,
        (fid_cred, pago_ini),
    )
    for pid, qty, pu, desc in (
        (pid_a, q_ca, 120.0, "Crédito parcial A"),
        (pid_b, q_cb, 85.0, "Crédito parcial B"),
    ):
        db.insert_movimiento_kardex(
            pid,
            "venta",
            -float(qty),
            ajustar_stock=True,
            referencia=nro_cred,
            factura_id=fid_cred,
            usuario="admin",
            tipo_codigo="FA",
            entidad_nombre="Cliente Prueba (crédito parcial)",
            bodega_codigo="Principal",
            precio_unitario=float(pu),
            descripcion_mov=f"Venta semilla: {nro_cred}",
            conn=conn,
        )

    conn.commit()
    conn.close()

    print(
        f"✅ Semilla de flujos aplicada (documentos anteriores {PREFIX} eliminados: {n_borrados}).\n"
        "\nDocumentos creados:\n"
        f"  • {nro_pres} — presupuesto (filtro «Solo presupuestos»). "
        f"Confirmar venta: «Pagar documento». Total RD$ {total_pres:,.2f}\n"
        f"  • {nro_vta} — emitida y saldada. Devoluciones: pestaña Devoluciones, buscar por número o id {fid_vta}\n"
        f"  • {nro_cred} — emitida con saldo RD$ {balance:,.2f} "
        f"(pagado {pago_ini:,.2f}). «Pagar documento» para abonar\n"
        "\nClientes:\n"
        f"  • {id_mail} — 40299887701 — facturacion.demo@prueba.local (prueba Correo)\n"
        f"  • {id_cred} — 40299887702 — cobro parcial\n"
        f"  • {id_lote} — 88877766601 — importación en lote (CSV en pestaña Facturas en lote)\n"
        "\nProductos (código para lote / catálogo):\n"
        f"  • {COD_PROD_A} — RD$ 120\n"
        f"  • {COD_PROD_B} — RD$ 85\n"
        "\nEjemplo CSV para «Facturas en lote» (una línea por ítem, mismo grupo = mismo presupuesto):\n"
        f"  1,88877766601,{COD_PROD_A},3\n"
        f"  1,88877766601,{COD_PROD_B},2\n"
        f"  2,88877766601,{COD_PROD_A},1\n"
        "\nArchivo de ejemplo en el proyecto: fixtures_lote_presupuestos.csv "
        "(péguelo en la pestaña Facturas en lote).\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Datos de prueba — flujos de facturación")
    parser.add_argument(
        "--limpiar",
        action="store_true",
        help="Solo elimina facturas y relaciones con número TEST-FLUJO-*",
    )
    args = parser.parse_args()
    try:
        db = Database()
    except Exception as e:
        print("Error al abrir la base:", e, file=sys.stderr)
        return 1
    if args.limpiar:
        conn = db.get_connection()
        cur = conn.cursor()
        n = _limpiar_documentos_prueba(cur)
        conn.commit()
        conn.close()
        print(f"Listo. Eliminados {n} documento(s) de prueba ({PREFIX}*).")
        return 0
    aplicar_seed(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
