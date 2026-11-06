import pytest
from fastapi import HTTPException

import web_server
from web_server import (
    ItemIn,
    LineaComboIn,
    _armar_lineas,
    _clave_valida,
    _descuento_promo,
    _filtro_fecha,
    _fondo_caja,
    _imagen_de,
    _partir_itbis,
    _precio,
    _puede,
    _resumen_permisos,
    _texto_componentes,
    _token_sesion,
    _usuario_de_token,
    conduces_en_cola,
)

from tests.conftest import insertar_producto


def _fila_precio(base=100, p2=0, p3=0, p4=0):
    fila = [None] * 15
    fila[2] = base
    fila[12], fila[13], fila[14] = p2, p3, p4
    return fila


def test_el_itbis_se_suma_o_se_saca_segun_el_precio():
    assert _partir_itbis(100, False, False) == (100.0, 0.0)
    assert _partir_itbis(100, True, False) == (100.0, 18.0)
    assert _partir_itbis(118, True, True) == (100.0, 18.0)
    assert _partir_itbis(-5, True, False) == (0.0, 0.0)


def test_el_precio_por_nivel_usa_el_guardado_o_el_descuento():
    fila = _fila_precio(100, 0, 80, 0)
    assert _precio(fila, 1) == 100
    assert _precio(fila, 2) == 98.0
    assert _precio(fila, 3) == 80
    assert _precio(fila, 9) == _precio(fila, 4)


def test_las_claves_de_permiso_y_el_resumen():
    assert _clave_valida("mostrador") is True
    assert _clave_valida("pedidos") is False
    assert _clave_valida("caja:ingreso") is True
    assert _clave_valida("caja:no-existe") is False
    assert _puede(["caja"], "caja:cerrar") is True
    assert _puede(["caja:ingreso"], "caja:ingreso") is True
    assert _puede(["inventario"], "caja") is False
    assert _resumen_permisos("admin", []) == "Todo"
    assert _resumen_permisos("mesero", []) == "Pedidos en la tablet"
    assert _resumen_permisos("user", []) == "Sin pantallas"
    assert "Ingreso de efectivo" in _resumen_permisos("user", ["caja:ingreso"])


def test_la_sesion_firma_el_usuario_y_caduca(monkeypatch):
    monkeypatch.setattr(web_server.time, "time", lambda: 1_700_000_000)
    token = _token_sesion("caja")
    assert _usuario_de_token(token) == "caja"
    assert _usuario_de_token(token + "x") is None
    assert _usuario_de_token("sin-firma") is None
    monkeypatch.setattr(
        web_server.time, "time", lambda: 1_700_000_000 + web_server._SESION_SEGUNDOS + 5
    )
    assert _usuario_de_token(token) is None


def test_el_fondo_de_caja_rechaza_vacio_y_negativo():
    assert _fondo_caja(0) == 0
    assert _fondo_caja(150.126) == 150.13
    with pytest.raises(HTTPException) as vacio:
        _fondo_caja(None)
    assert vacio.value.status_code == 400
    with pytest.raises(HTTPException):
        _fondo_caja(-1)


def test_el_filtro_de_fechas_usa_el_rango_o_los_dias():
    sql, params = _filtro_fecha("2026-01-01", "2026-01-31", 7, "f.fecha")
    assert "date(f.fecha)" in sql
    assert params == ["2026-01-01", "2026-01-31"]
    sql_dias, params_dias = _filtro_fecha("", "", 15, "f.fecha")
    assert params_dias == ["-15 days"]


def test_la_imagen_cae_en_el_fondo_del_producto():
    assert _imagen_de("Cerveza Presidente") == "/estaticos/productos/cerveza.jpg"
    assert _imagen_de("Servilleta") == ""


def test_el_texto_del_combo_junta_los_componentes():
    texto = _texto_componentes([(1, 2, "Cerveza"), (2, 1, "Hielo")])
    assert texto == "Cerveza ×2 + Hielo ×1"


def test_la_promo_descuento_el_bruto(base):
    conn = base.get_connection()
    cur = conn.cursor()
    producto = insertar_producto(cur, "PROMO-1", "Cerveza promo", stock=20, precio=100)
    conn.commit()
    base.guardar_promocion("2x1 cerveza", "2x1", 0, producto_id=producto)
    assert _descuento_promo(cur, producto, 5, 100, 500) == 200
    conn.close()


def test_armar_lineas_se_salta_combos_y_cantidades_vacias(base):
    conn = base.get_connection()
    cur = conn.cursor()
    producto = insertar_producto(cur, "LIN-1", "Agua", stock=5, precio=50)
    conn.commit()
    detalle, subtotal, impuesto = _armar_lineas(
        cur,
        [
            ItemIn(id=producto, cantidad=2, nivel=1),
            ItemIn(id=producto, cantidad=0, nivel=1),
            ItemIn(id=999999, cantidad=1, nivel=1),
        ],
    )
    conn.close()
    assert len(detalle) == 1
    assert subtotal == 100
    assert impuesto == 18


def test_una_linea_de_combo_sin_cantidad_no_pasa(base):
    conn = base.get_connection()
    cur = conn.cursor()
    with pytest.raises(HTTPException):
        web_server._lineas_combo(cur, [LineaComboIn(producto_id=1, cantidad=0)])
    conn.close()


def test_la_cola_descarta_mesas_sin_codigo():
    cola = conduces_en_cola(
        [
            {"id": 2, "codigo": "C0002", "fecha": "2026-01-02"},
            {"id": 1, "codigo": "  ", "fecha": "2026-01-01"},
        ]
    )
    assert [x["codigo"] for x in cola] == ["C0002"]
