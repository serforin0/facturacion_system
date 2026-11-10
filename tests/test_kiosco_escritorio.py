import os

from kiosco_escritorio import marcar_salida_tecnico, modo_kiosco, tecla_bloqueada


def test_el_kiosco_solo_entra_en_windows_cuando_la_pc_esta_dedicada(tmp_path):
    assert modo_kiosco(["main.py", "--kiosco"], plataforma="darwin") is False
    assert modo_kiosco(["main.py"], plataforma="win32", carpeta=str(tmp_path)) is False
    assert modo_kiosco(["main.py", "--kiosco"], plataforma="win32", carpeta=str(tmp_path)) is True
    (tmp_path / "kiosco.flag").write_text("", encoding="utf-8")
    assert modo_kiosco(["SistemaFacturacion.exe"], plataforma="win32", carpeta=str(tmp_path)) is True


def test_las_teclas_de_salir_de_windows_se_tragan_y_el_resto_pasa():
    assert tecla_bloqueada(0x5B, False, False, False) == "bloquear"
    assert tecla_bloqueada(0x09, True, False, False) == "bloquear"
    assert tecla_bloqueada(0x73, True, False, False) == "bloquear"
    assert tecla_bloqueada(0x1B, False, True, True) == "bloquear"
    assert tecla_bloqueada(0x7B, False, True, True) == "salir"
    assert tecla_bloqueada(ord("A"), False, False, False) == ""
    assert tecla_bloqueada(0x0D, False, False, False) == ""


def test_la_salida_del_tecnico_deja_una_marca_para_no_reabrir(tmp_path):
    ruta = marcar_salida_tecnico(str(tmp_path))
    assert os.path.isfile(ruta)
    assert "tecnico" in open(ruta, encoding="utf-8").read()
