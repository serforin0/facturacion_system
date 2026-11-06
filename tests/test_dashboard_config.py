import json

import dashboard_config_store as store


def test_la_configuracion_nueva_trae_los_modulos_del_panel(tmp_path, monkeypatch):
    ruta = tmp_path / "dashboard_config.json"
    monkeypatch.setattr(store, "_config_path", lambda: str(ruta))
    cfg = store.load_config()
    ids = {m["id"] for m in cfg["modules"]}
    assert {"facturacion", "inventario", "caja", "usuarios"} <= ids
    assert cfg["grid_columns"] == 3
    assert ruta.is_file()


def test_un_json_roto_vuelve_a_los_valores_por_defecto(tmp_path, monkeypatch):
    ruta = tmp_path / "dashboard_config.json"
    ruta.write_text("{", encoding="utf-8")
    monkeypatch.setattr(store, "_config_path", lambda: str(ruta))
    cfg = store.load_config()
    assert cfg["company_name"] == "Esquina Tropical"


def test_los_huecos_no_se_pisan_y_lo_guardado_se_mezcla(tmp_path, monkeypatch):
    ruta = tmp_path / "dashboard_config.json"
    monkeypatch.setattr(store, "_config_path", lambda: str(ruta))
    base = store.default_config()
    base["company_name"] = "Bar de prueba"
    base["modules"] = [
        {"id": "facturacion", "label": "Cobrar", "slot": 0, "visible": True},
        {"id": "caja", "label": "Caja", "slot": 0, "visible": False},
    ]
    store.save_config(base)
    cfg = store.load_config()
    assert cfg["company_name"] == "Bar de prueba"
    por_id = {m["id"]: m for m in cfg["modules"]}
    assert por_id["facturacion"]["label"] == "Cobrar"
    assert por_id["facturacion"]["slot"] != por_id["caja"]["slot"]
    assert "inventario" in por_id
    guardado = json.loads(ruta.read_text(encoding="utf-8"))
    assert guardado["company_name"] == "Bar de prueba"
