from generar_botellas import _partir_nombre, colores


def test_el_color_sigue_el_nombre_y_si_no_la_categoria():
    assert colores("Cerveza Corona blue", "cervezas")[0] == (46, 108, 196)
    assert colores("Barceló", "rones") == colores("Otro", "rones")
    assert len(colores("Desconocido", "sin-categoria")) == 3


def test_el_nombre_de_la_botella_separa_el_volumen():
    assert _partir_nombre("Ron Barceló 750 ml") == ("Barceló", "750 ml")
    assert _partir_nombre("Vodka Absolut 1 lt") == ("Absolut", "1 lt")
    assert _partir_nombre("  Hielo   bolsa  ")[0] == "Hielo bolsa"
