"""
Carga datos de demostración cuando la base está sin productos (instalación nueva).
Inventario, kardex, facturas recientes, clientes, proveedores, caja cerrada, promoción y combo.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta


def _dt(days_ago: int = 0, hour: int = 14, minute: int = 30) -> str:
    t = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    t = t - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _line_itbis(cant: float, precio: float, desc: float = 0.0, aplica: bool = True):
    sub = round(cant * precio - desc, 2)
    imp = round(sub * 0.18, 2) if aplica else 0.0
    total = round(sub + imp, 2)
    return sub, imp, total


def apply_demo_seed_if_empty(cursor) -> bool:
    cursor.execute("SELECT COUNT(*) FROM productos")
    if cursor.fetchone()[0] > 0:
        return False

    # ——— Categorías extra ———
    for cat in ("Snacks", "Hielo"):
        cursor.execute("INSERT OR IGNORE INTO categorias (nombre) VALUES (?)", (cat,))

    def cat_id(nombre: str) -> int:
        cursor.execute("SELECT id FROM categorias WHERE nombre = ?", (nombre,))
        row = cursor.fetchone()
        return row[0] if row else 1

    # ——— Proveedores (además del «Sin proveedor» que ya crea la migración) ———
    prov_rows = [
        ("Distribuidora Caribe Demo", "RNC-131313131", "809-555-0101"),
        ("Embotelladora Nacional Demo", "RNC-402020202", "809-555-0202"),
    ]
    prov_ids = []
    for nom, doc, tel in prov_rows:
        cursor.execute(
            "SELECT id FROM proveedores WHERE nombre = ?",
            (nom,),
        )
        r = cursor.fetchone()
        if r:
            prov_ids.append(r[0])
        else:
            cursor.execute(
                """
                INSERT INTO proveedores (nombre, documento, telefono, activo)
                VALUES (?, ?, ?, 1)
                """,
                (nom, doc, tel),
            )
            prov_ids.append(cursor.lastrowid)

    pid_main = prov_ids[0]

    # ——— Productos: stock final = suma algebraica de movimientos que insertamos después ———
    prods = [
        {
            "codigo": "DEMO-CERV-01",
            "nombre": "Cerveza Presidente 12oz (demo)",
            "desc": "Lata 355ml — datos de prueba",
            "precio": 95.0,
            "base": 62.0,
            "min_p": 75.0,
            "stock": 174,
            "smin": 24,
            "cat": "Cerveza",
            "cb": "7501000123456",
            "bod": "Principal",
        },
        {
            "codigo": "DEMO-REF-01",
            "nombre": "Refresco Cola 350ml (demo)",
            "desc": "Botella — prueba reportes",
            "precio": 55.0,
            "base": 35.0,
            "min_p": 45.0,
            "stock": 220,
            "smin": 40,
            "cat": "Refresco",
            "cb": "7501000123457",
            "bod": "Principal",
        },
        {
            "codigo": "DEMO-LIC-01",
            "nombre": "Ron Añejo 750ml (demo)",
            "desc": "Stock bajo a propósito",
            "precio": 890.0,
            "base": 620.0,
            "min_p": 800.0,
            "stock": 18,
            "smin": 20,
            "cat": "Licor",
            "cb": "7501000123458",
            "bod": "Principal",
        },
        {
            "codigo": "DEMO-AGUA-01",
            "nombre": "Agua 600ml (demo)",
            "desc": None,
            "precio": 25.0,
            "base": 12.0,
            "min_p": 18.0,
            "stock": 400,
            "smin": 50,
            "cat": "Agua",
            "cb": "7501000123459",
            "bod": "Barra",
        },
        {
            "codigo": "DEMO-VIN-01",
            "nombre": "Vino tinto botella (demo)",
            "desc": None,
            "precio": 450.0,
            "base": 300.0,
            "min_p": 400.0,
            "stock": 36,
            "smin": 6,
            "cat": "Vino",
            "cb": "7501000123460",
            "bod": "Principal",
        },
        {
            "codigo": "DEMO-SNACK-01",
            "nombre": "Papas fritas 40g (demo)",
            "desc": "Por debajo del mínimo",
            "precio": 35.0,
            "base": 15.0,
            "min_p": 28.0,
            "stock": 8,
            "smin": 10,
            "cat": "Snacks",
            "cb": "7501000123461",
            "bod": "Principal",
        },
        {
            "codigo": "DEMO-HIELO-01",
            "nombre": "Bolsa hielo 5lb (demo)",
            "desc": None,
            "precio": 150.0,
            "base": 80.0,
            "min_p": 120.0,
            "stock": 45,
            "smin": 5,
            "cat": "Hielo",
            "cb": "7501000123462",
            "bod": "Principal",
        },
    ]

    prod_by_code = {p["codigo"]: p for p in prods}
    code_to_id = {}
    for p in prods:
        cursor.execute(
            """
            INSERT INTO productos (
                nombre, descripcion, precio, precio_base, precio_minimo, stock, categoria_id,
                stock_minimo, codigo_barras, activo, codigo_producto, tipo_producto, unidad_medida,
                bodega_codigo, aplica_itbis, facturar_sin_stock, proveedor_id, precio_2
            ) VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?,1,1,?,?)
            """,
            (
                p["nombre"],
                p["desc"],
                p["precio"],
                p["base"],
                p["min_p"],
                p["stock"],
                cat_id(p["cat"]),
                p["smin"],
                p["cb"],
                p["codigo"],
                "Físico",
                "Unidad",
                p["bod"],
                pid_main,
                round(p["precio"] * 0.95, 2),
            ),
        )
        code_to_id[p["codigo"]] = cursor.lastrowid

    def pid(code: str) -> int:
        return code_to_id[code]

    # ——— Clientes ———
    clientes = [
        ("Bar La Esquina SRL", "131111111", "rnc", "809-555-1000", "ventas@demo.local", None),
        ("María Pérez", "00123456789", "cedula", "809-555-2000", None, "Santo Domingo"),
        ("Comercial RD SA", "402123456", "rnc", "809-555-3000", "comercial@demo.local", None),
    ]
    cliente_ids = []
    for nom, doc, tdoc, tel, em, dir_ in clientes:
        cursor.execute(
            """
            INSERT INTO clientes (nombre, documento, tipo_documento, telefono, email, direccion)
            VALUES (?,?,?,?,?,?)
            """,
            (nom, doc, tdoc, tel, em, dir_),
        )
        cliente_ids.append(cursor.lastrowid)

    # ——— ~90 días: kardex denso (CO/RT) + muchas facturas (FA) para reportes e indicadores ———
    prod_codes = [p["codigo"] for p in prods]
    target_stocks = {p["codigo"]: float(p["stock"]) for p in prods}
    rng = random.Random(42)
    events = []

    for _ in range(36):
        d = rng.randint(8, 88)
        c = rng.choice(prod_codes)
        if rng.random() < 0.68:
            q = float(rng.randint(8, 36))
            ent = (
                "Distribuidora Caribe Demo"
                if rng.random() < 0.55
                else "Embotelladora Nacional Demo"
            )
            events.append(
                (
                    "mov",
                    d,
                    c,
                    "ingreso",
                    "CO",
                    q,
                    f"Compra demo OC-{rng.randint(1000, 9999)}",
                    ent,
                    prod_by_code[c]["bod"],
                    prod_by_code[c]["base"],
                )
            )
        else:
            q = -float(rng.randint(1, 6))
            events.append(
                (
                    "mov",
                    d,
                    c,
                    "retiro",
                    "RT",
                    q,
                    "Retiro — "
                    + rng.choice(["Merma / daño", "Uso interno", "Degustación", "Ajuste inventario"]),
                    rng.choice(["Merma / daño", "Uso interno"]),
                    prod_by_code[c]["bod"],
                    None,
                )
            )

    for i in range(52):
        d = rng.randint(0, 88)
        lines = []
        for _ in range(rng.randint(1, 3)):
            c = rng.choice(prod_codes)
            mx = 3 if c == "DEMO-LIC-01" else 14
            lines.append((c, float(rng.randint(1, mx)), prod_by_code[c]["precio"], 0.0))
        cli_ix = rng.choice([None, None, 0, 1, 2])
        events.append(("inv", d, i, lines, cli_ix))

    for d in range(7):
        c = rng.choice(prod_codes)
        q = float(rng.randint(5, 20))
        events.append(
            (
                "inv",
                d,
                900 + d,
                [(c, q, prod_by_code[c]["precio"], 0.0)],
                None,
            )
        )

    def _delta_sin_apertura(ev_list, codes):
        delta = {x: 0.0 for x in codes}
        for e in ev_list:
            if e[0] == "mov":
                delta[e[2]] += e[5]
            else:
                for c, q, _, _ in e[3]:
                    delta[c] -= q
        return delta

    def _shrink_one_unit(ev_list, code):
        for i in range(len(ev_list) - 1, -1, -1):
            e = ev_list[i]
            if e[0] != "inv":
                continue
            lines = list(e[3])
            for j, (c, q, p, desc) in enumerate(lines):
                if c != code or q < 1:
                    continue
                if q > 1:
                    lines[j] = (c, q - 1, p, desc)
                    ev_list[i] = ("inv", e[1], e[2], lines, e[4])
                    return True
                del lines[j]
                if not lines:
                    del ev_list[i]
                else:
                    ev_list[i] = ("inv", e[1], e[2], lines, e[4])
                return True
        return False

    def _shrink_synthetic_ingreso(ev_list, code):
        for i in range(len(ev_list) - 1, -1, -1):
            e = ev_list[i]
            if e[0] != "mov" or e[3] != "ingreso" or e[2] != code:
                continue
            q = e[5]
            if q > 20:
                nq = q - 12
                ev_list[i] = (e[0], e[1], e[2], e[3], e[4], nq, e[6], e[7], e[8], e[9])
                return True
            if q > 5:
                del ev_list[i]
                return True
        return False

    def _drop_one_ingreso(ev_list, code):
        for i in range(len(ev_list) - 1, -1, -1):
            e = ev_list[i]
            if e[0] == "mov" and e[3] == "ingreso" and e[2] == code:
                del ev_list[i]
                return True
        return False

    # delta[c] > stock[c] ⇒ saldo inicial negativo: hay demasiados ingresos vs. ventas
    for _ in range(25000):
        delta = _delta_sin_apertura(events, prod_codes)
        worst_ex, worst_c = None, None
        for c in prod_codes:
            ex = delta[c] - target_stocks[c]
            if ex > 1e-6 and (worst_ex is None or ex > worst_ex):
                worst_ex, worst_c = ex, c
        if worst_ex is None or worst_ex <= 1e-6:
            break
        if _shrink_synthetic_ingreso(events, worst_c):
            continue
        if _drop_one_ingreso(events, worst_c):
            continue
        events.append(
            (
                "mov",
                25,
                worst_c,
                "retiro",
                "RT",
                -30.0,
                "Ajuste demo — salida por conciliación de semilla",
                "Ajuste inventario",
                prod_by_code[worst_c]["bod"],
                None,
            )
        )
    else:
        raise RuntimeError("demo_seed: no convergió el ajuste de stock demo (exceso ingresos)")

    delta = _delta_sin_apertura(events, prod_codes)
    opening_moves = []
    for c in prod_codes:
        oqty = target_stocks[c] - delta[c]
        if oqty > 0.0001:
            opening_moves.append(
                (
                    "mov",
                    91,
                    c,
                    "ingreso",
                    "CO",
                    oqty,
                    "Saldo inicial inventario (semilla ~90 días)",
                    "Apertura inventario demo",
                    prod_by_code[c]["bod"],
                    prod_by_code[c]["base"],
                )
            )

    timeline = opening_moves + events

    def _sort_key(ev):
        if ev[0] == "mov":
            return (-ev[1], 0 if ev[3] == "ingreso" else 1, 0)
        return (-ev[1], 2, ev[2])

    timeline.sort(key=_sort_key)

    fnum = 0
    for ev in timeline:
        if ev[0] == "mov":
            _, day, code, tmov, tc, qty, dmov, ent, bod, pu = ev
            cursor.execute(
                """
                INSERT INTO movimientos_inventario (
                    producto_id, tipo_movimiento, cantidad, referencia, factura_id, fecha, usuario,
                    descripcion_mov, tipo_codigo, entidad_nombre, bodega_codigo, precio_unitario
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid(code),
                    tmov,
                    qty,
                    f"K-{code}-{day}"[:40],
                    None,
                    _dt(days_ago=day, hour=9 + (day % 7)),
                    "admin",
                    dmov,
                    tc,
                    ent,
                    bod,
                    pu,
                ),
            )
        else:
            _, day, _, lines, cli_ix = ev
            fnum += 1
            numero = f"DEMO-F-2025-HIST-{fnum:04d}"
            subtotal = 0.0
            imp_total = 0.0
            detalle_rows = []
            for code, cant, precio, desc in lines:
                sb, im, tl = _line_itbis(cant, precio, desc, True)
                subtotal += sb
                imp_total += im
                detalle_rows.append((code, cant, precio, desc, sb, im, tl))
            subtotal = round(subtotal, 2)
            imp_total = round(imp_total, 2)
            total = round(subtotal + imp_total, 2)
            cli_id = cliente_ids[cli_ix] if cli_ix is not None else None

            cursor.execute(
                """
                INSERT INTO facturas (
                    numero, fecha, tipo_comprobante, cliente_id,
                    subtotal, descuento_total, impuesto_total, total, estado, usuario, caja
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    numero,
                    _dt(days_ago=day, hour=11 + (fnum % 8)),
                    "consumidor_final",
                    cli_id,
                    subtotal,
                    0.0,
                    imp_total,
                    total,
                    "emitida",
                    "admin",
                    "Caja 1",
                ),
            )
            fid = cursor.lastrowid

            for code, cant, precio, desc, sb, im, tl in detalle_rows:
                cursor.execute(
                    """
                    INSERT INTO factura_detalle (
                        factura_id, producto_id, descripcion, cantidad,
                        precio_unitario, descuento_item, impuesto_item, total_linea
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        fid,
                        pid(code),
                        prod_by_code[code]["nombre"],
                        cant,
                        precio,
                        desc,
                        im,
                        tl,
                    ),
                )

            if rng.random() < 0.22 and total > 80:
                m1 = round(total * 0.55, 2)
                m2 = round(total - m1, 2)
                cursor.execute(
                    """
                    INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia)
                    VALUES (?,?,?,?)
                    """,
                    (fid, "efectivo", m1, None),
                )
                cursor.execute(
                    """
                    INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia)
                    VALUES (?,?,?,?)
                    """,
                    (fid, "tarjeta", m2, "demo"),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO pagos_factura (factura_id, tipo_pago, monto, referencia)
                    VALUES (?,?,?,?)
                    """,
                    (fid, "efectivo", total, None),
                )

            fe = _dt(days_ago=day, hour=11 + (fnum % 8))
            for code, cant, precio, desc, sb, im, tl in detalle_rows:
                cursor.execute(
                    """
                    INSERT INTO movimientos_inventario (
                        producto_id, tipo_movimiento, cantidad, referencia, factura_id, fecha, usuario,
                        descripcion_mov, tipo_codigo, entidad_nombre, bodega_codigo, precio_unitario
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        pid(code),
                        "venta",
                        -float(cant),
                        numero,
                        fid,
                        fe,
                        "admin",
                        f"Venta: Factura {numero}",
                        "FA",
                        "Cliente consumidor final"
                        if cli_id is None
                        else clientes[cli_ix][0],
                        prod_by_code[code]["bod"],
                        precio,
                    ),
                )

    # ——— Promoción demo (categoría Refresco) ———
    fin_promo = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO promociones (
            nombre, descripcion, tipo_descuento, valor,
            fecha_inicio, fecha_fin, aplica_por, activo
        ) VALUES (?,?,?,?,?,?,?,1)
        """,
        (
            "Demo: 10% en Refrescos",
            "Descuento de prueba sobre categoría Refresco",
            "porcentaje",
            10.0,
            _dt(days_ago=85, hour=8),
            fin_promo,
            "categoria",
        ),
    )
    prom_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO promociones_detalle (promocion_id, producto_id, categoria_id, cliente_id)
        VALUES (?,NULL,?,NULL)
        """,
        (prom_id, cat_id("Refresco")),
    )

    # ——— Combo demo ———
    cursor.execute(
        """
        INSERT INTO combos (nombre, descripcion, precio_combo, activo)
        VALUES (?,?,?,1)
        """,
        (
            "Combo Demo Cubetazo",
            "Cerveza + refresco + hielo (precio de prueba)",
            1200.0,
        ),
    )
    combo_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO combos_detalle (combo_id, producto_id, cantidad) VALUES (?,?,?)
        """,
        (combo_id, pid("DEMO-CERV-01"), 6.0),
    )
    cursor.execute(
        """
        INSERT INTO combos_detalle (combo_id, producto_id, cantidad) VALUES (?,?,?)
        """,
        (combo_id, pid("DEMO-REF-01"), 6.0),
    )
    cursor.execute(
        """
        INSERT INTO combos_detalle (combo_id, producto_id, cantidad) VALUES (?,?,?)
        """,
        (combo_id, pid("DEMO-HIELO-01"), 1.0),
    )

    # ——— Cierres de caja demo (histórico en varios meses) ———
    cierres_demo = [
        (
            "Caja Principal — Turno mañana",
            78,
            78,
            800.0,
            12800.0,
            10200.0,
            2200.0,
            400.0,
            11000.0,
            0.0,
            "Cierre demo — período anterior",
        ),
        (
            "Caja Barra — Fin de semana",
            52,
            52,
            400.0,
            9600.0,
            7800.0,
            1600.0,
            200.0,
            8200.0,
            0.0,
            "Cierre demo — tarjeta y efectivo",
        ),
        (
            "Caja Demo Turno reciente",
            18,
            18,
            500.0,
            4200.0,
            3800.0,
            400.0,
            0.0,
            4300.0,
            0.0,
            "Cierre de demostración — cuadró",
        ),
    ]
    for nom, d_ap, d_ci, mi, tv, tef, ttar, tot, ec, diff, obs in cierres_demo:
        cursor.execute(
            """
            INSERT INTO cierres_caja (
                nombre_caja, fecha_apertura, fecha_cierre,
                usuario_apertura, usuario_cierre,
                monto_inicial, total_ventas,
                total_efectivo_sistema, total_tarjeta_sistema, total_otros_sistema,
                efectivo_contado, diferencia_efectivo, observaciones, estado
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                nom,
                _dt(days_ago=d_ap, hour=7),
                _dt(days_ago=d_ci, hour=16),
                "admin",
                "admin",
                mi,
                tv,
                tef,
                ttar,
                tot,
                ec,
                diff,
                obs,
                "cerrado",
            ),
        )

    print(
        "📦 Demo: ~90 días de kardex (CO/RT/FA), ~60 facturas, resumen mensual en reportes, "
        "cierres de caja históricos. Usuario: admin / admin07!"
    )
    return True
