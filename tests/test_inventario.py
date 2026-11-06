from cargar_inventario_barra import _cubre, _normalizar, _puntaje, _tokens
from inventario_barra import PRODUCTOS
from subir_inventario_barra import categoria


def test_el_conteo_de_barra_no_repite_codigos():
    codigos = [fila[0] for fila in PRODUCTOS]
    assert len(codigos) == len(set(codigos))
    assert len(PRODUCTOS) > 20
    for codigo, unidad, nombre, stock, costo, cat, busqueda in PRODUCTOS:
        assert codigo and unidad and nombre and cat and busqueda
        assert stock >= 0
        assert costo > 0


def test_la_busqueda_de_foto_exige_el_nombre_y_rechaza_logos():
    assert "absolut" in _tokens("Absolut Vodka")
    assert _normalizar("Añejo") == "anejo"
    assert _cubre({"absolut", "vodka"}, {"absolut", "vodka", "bottle"}) is True
    assert _cubre({"absolut"}, {"vodka"}) is False
    assert _puntaje("Absolut Vodka bottle", "Absolut Vodka bottle") > 0
    assert _puntaje("Company logo collection", "Absolut Vodka bottle") == -1


def test_la_categoria_de_carga_reutiliza_la_que_ya_existe(base):
    conn = base.get_connection()
    cur = conn.cursor()
    primera = categoria(cur, "  Rones  ")
    segunda = categoria(cur, "Rones")
    vacia = categoria(cur, " ")
    conn.commit()
    conn.close()
    assert primera == segunda
    assert vacia != primera
