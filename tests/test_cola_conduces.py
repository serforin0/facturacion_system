from web_server import conduces_en_cola


def test_la_cola_de_caja_solo_trae_pedidos_guardados_y_el_mas_viejo_primero():
    items = [
        {"id": 3, "codigo": "C0003", "mesa": "Mesa 3", "fecha": "2026-10-25 13:10:00", "total": 80},
        {"id": 9, "codigo": "", "mesa": "Mesa 9", "fecha": "2026-10-25 13:20:00", "total": 0},
        {"id": 2, "codigo": "C0002", "mesa": "Mesa 2", "fecha": "2026-10-25 12:00:00", "total": 150},
        {"id": 4, "codigo": "   ", "mesa": "Mesa 4", "fecha": "2026-10-25 11:00:00", "total": 0},
    ]
    cola = conduces_en_cola(items)
    assert [x["codigo"] for x in cola] == ["C0002", "C0003"]
