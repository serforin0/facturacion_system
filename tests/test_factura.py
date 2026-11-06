import pytest

from factura_manager import (
    _parse_monto_pago,
    linea_ticket_desde_conduce,
    marcar_conduce_facturado,
    normalizar_codigo_conduce,
)


def test_el_codigo_del_pedido_se_normaliza():
    assert normalizar_codigo_conduce("c42") == "C0042"
    assert normalizar_codigo_conduce("C0042") == "C0042"
    assert normalizar_codigo_conduce(" mesa ") == "MESA"


def test_el_monto_acepta_miles_y_coma_decimal():
    assert _parse_monto_pago("53,141.30") == 53141.30
    assert _parse_monto_pago("53141,30") == 53141.30
    assert _parse_monto_pago("RD$ 10.50") == 10.50
    assert _parse_monto_pago("  ") == 0.0


def test_la_linea_del_mesero_entra_al_ticket_con_el_itbis_incluido():
    assert linea_ticket_desde_conduce({"cantidad": 0, "precio": 10}) is None
    item = linea_ticket_desde_conduce(
        {"id": 7, "nombre": "Cerveza", "cantidad": 2, "precio": 100, "total": 200, "itbis": True}
    )
    assert item["cantidad"] == 2
    assert item["total_linea"] == 200
    assert item["impuesto_item"] == round(item["subtotal_neto"] * 0.18, 2)
    combo = linea_ticket_desde_conduce(
        {"id": 3, "nombre": "Cubo", "cantidad": 1, "precio": 350, "total": 350, "combo": True, "itbis": False},
        [{"id": 7, "cantidad": 6}],
    )
    assert combo["id"] is None
    assert combo["combo_id"] == 3
    assert combo["componentes"] == [{"id": 7, "cantidad": 6.0}]
    assert combo["impuesto_item"] == 0


def test_facturar_el_pedido_suelta_la_mesa_y_no_se_puede_repetir(base):
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO conduces (codigo, mesa, usuario, items_json, subtotal, itbis, total, estado)
        VALUES ('C0099', 'Mesa 4', 'mesero', '[]', 100, 18, 118, 'pendiente')
        """
    )
    conduce = cur.lastrowid
    cur.execute(
        "INSERT INTO mesa_ocupada (mesa, usuario, conduce_id, desde) VALUES ('Mesa 4', 'mesero', ?, '2026-01-01')",
        (conduce,),
    )
    conn.commit()
    marcar_conduce_facturado(cur, conduce, 55)
    estado = cur.execute("SELECT estado, factura_id FROM conduces WHERE id=?", (conduce,)).fetchone()
    ocupada = cur.execute("SELECT COUNT(*) FROM mesa_ocupada WHERE conduce_id=?", (conduce,)).fetchone()[0]
    conn.commit()
    assert estado == ("facturado", 55)
    assert ocupada == 0
    with pytest.raises(ValueError):
        marcar_conduce_facturado(cur, conduce, 56)
    with pytest.raises(ValueError):
        marcar_conduce_facturado(cur, 999999, 1)
    conn.close()
