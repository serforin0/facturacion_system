import sys

import app_paths


def test_en_desarrollo_los_datos_viven_en_el_proyecto():
    assert app_paths.is_frozen() is False
    assert app_paths.data_directory().endswith("facturacion_system")


def test_empaquetada_los_datos_viven_junto_al_ejecutable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/app/facturacion.exe")
    assert app_paths.is_frozen() is True
    assert app_paths.data_directory() == "/tmp/app"
