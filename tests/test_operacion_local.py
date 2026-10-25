from operacion_local import (
    autorizacion_ok,
    aviso_stock,
    limpiar_nota,
    partir_lineas,
    propina_legal,
    puntos_ganados,
    rebaja_puntos,
)


def test_la_propina_es_el_diez_por_ciento_y_no_sale_si_esta_apagada():
    assert propina_legal(150.5, True) == 15.05
    assert propina_legal(150.5, False) == 0


def test_partir_deja_una_parte_en_la_mesa():
    lineas = [{"id": 1, "nombre": "Cerveza"}, {"id": 2, "nombre": "Burger"}]
    van, quedan = partir_lineas(lineas, [0])
    assert [x["nombre"] for x in van] == ["Cerveza"]
    assert [x["nombre"] for x in quedan] == ["Burger"]


def test_partir_exige_las_dos_partes():
    try:
        partir_lineas([{"id": 1}], [0])
    except ValueError:
        return
    raise AssertionError("debía rechazar llevarse toda la mesa")


def test_stock_bajo_cuando_llega_al_minimo():
    assert aviso_stock(2, 5) is True
    assert aviso_stock(6, 5) is False
    assert aviso_stock(None, 5) is False


def test_puntos_y_rebaja():
    assert puntos_ganados(250) == 2
    assert rebaja_puntos(40, 15) == (15, 15.0)
    assert rebaja_puntos(10, 40) == (10, 10.0)


def test_el_extra_suma_al_precio_y_el_asiento_cuadra():
    from operacion_local import asiento_venta, precio_con_extras

    precio, texto = precio_con_extras(100, [{"nombre": "Extra queso", "precio": 40}])
    assert precio == 140
    assert texto == "Extra queso"
    lineas = asiento_venta(caja=118, banco=0, saldo=0, fiado=0, ventas=100, itbis=18, propina=0)
    assert round(sum(d for _, d, _ in lineas), 2) == round(sum(c for _, _, c in lineas), 2)


def test_la_tarjeta_pide_autorizacion_y_la_nota_se_recorta():
    assert autorizacion_ok(0, "") == ""
    assert autorizacion_ok(100, "AB-1234") == "AB1234"
    try:
        autorizacion_ok(100, "12")
    except ValueError:
        pass
    else:
        raise AssertionError("una autorización corta debía fallar")
    assert limpiar_nota("  sin   hielo  ") == "sin hielo"
