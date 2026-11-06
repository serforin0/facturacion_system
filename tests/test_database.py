import contextlib
import io
import os

import pytest

import database
from database import Database, _tipo_codigo_default
from tests.conftest import insertar_producto


def test_la_base_relativa_no_depende_de_la_carpeta_actual(tmp_path, monkeypatch):
    otra = tmp_path / "otra"
    otra.mkdir()
    monkeypatch.setattr(database, "data_directory", lambda: str(tmp_path))
    monkeypatch.chdir(otra)
    with contextlib.redirect_stdout(io.StringIO()):
        base = Database("caja.db")
    assert base.db_name == os.path.join(str(tmp_path), "caja.db")
    assert (tmp_path / "caja.db").is_file()
    assert not (otra / "caja.db").exists()


def test_una_ruta_absoluta_de_base_se_respeta(tmp_path):
    ruta = tmp_path / "custom.db"
    with contextlib.redirect_stdout(io.StringIO()):
        base = Database(str(ruta))
    assert base.db_name == str(ruta)


def test_el_codigo_de_movimiento_sale_del_tipo():
    assert _tipo_codigo_default("venta") == "FA"
    assert _tipo_codigo_default("compra") == "CO"
    assert _tipo_codigo_default("retiro") == "RT"
    assert _tipo_codigo_default("traslado") == "TR"
    assert _tipo_codigo_default("") == ""


def test_el_login_acepta_al_admin_y_rechaza_la_clave(base):
    assert base.validate_user("admin", "admin07!") == "admin"
    assert base.validate_user("admin", "no") is None
    base.create_user("cajero1", "clave", "user")
    assert base.validate_user("cajero1", "clave") == "user"
    with pytest.raises(ValueError):
        base.create_user("cajero1", "otra", "user")


def test_la_configuracion_se_guarda_y_se_lee(base):
    assert base.get_config("no_existe", "0") == "0"
    base.set_config("empresa_rnc", "101010101")
    base.set_config("empresa_rnc", "131415161")
    assert base.get_config("empresa_rnc") == "131415161"


def test_el_ncf_avanza_y_se_detiene_al_llegar_al_tope(base):
    primero = base.tomar_siguiente_ncf("consumidor_final")
    segundo = base.tomar_siguiente_ncf("consumidor_final")
    assert primero.startswith("B02")
    assert int(segundo[-8:]) == int(primero[-8:]) + 1
    base.guardar_secuencia_ncf("consumidor_final", "B02", 2, 1)
    assert base.tomar_siguiente_ncf("consumidor_final") is None
    assert base.tomar_siguiente_ncf("tipo_que_no_existe") is None


def test_la_compra_sube_el_stock_y_deja_el_asiento(base):
    conn = base.get_connection()
    cur = conn.cursor()
    producto = insertar_producto(cur, "COMP-1", "Hielo", stock=3, precio=40)
    conn.commit()
    conn.close()
    proveedor = base.crear_proveedor("Nevera del barrio", "101", "8090000000")
    compra = base.registrar_compra(proveedor, producto, 4, 25, "BARRA", "admin", "reposición")
    conn = base.get_connection()
    stock = conn.execute("SELECT stock FROM productos WHERE id=?", (producto,)).fetchone()[0]
    bodega = conn.execute(
        "SELECT cantidad FROM stock_bodega WHERE producto_id=? AND bodega='BARRA'",
        (producto,),
    ).fetchone()[0]
    asiento = conn.execute(
        "SELECT concepto FROM asientos WHERE origen='compra' AND origen_id=?",
        (compra,),
    ).fetchone()
    conn.close()
    assert float(stock) == 7
    assert float(bodega) == 7
    assert asiento[0].startswith("Compra")
    with pytest.raises(ValueError):
        base.registrar_compra(proveedor, producto, 0, 25, "BARRA", "admin")


def test_el_traslado_no_deja_sacar_mas_de_lo_que_hay(base):
    conn = base.get_connection()
    cur = conn.cursor()
    producto = insertar_producto(cur, "MOV-1", "Ron", stock=10, precio=200, bodega="Principal")
    conn.commit()
    conn.close()
    base.ajustar_stock_bodega(producto, 0, "Principal")
    with pytest.raises(ValueError):
        base.transferir_entre_bodegas(producto, "Principal", "Principal", 1, "admin")
    with pytest.raises(ValueError):
        base.transferir_entre_bodegas(producto, "Principal", "BARRA", 11, "admin")
    base.transferir_entre_bodegas(producto, "Principal", "BARRA", 3, "admin")
    conn = base.get_connection()
    origen = conn.execute(
        "SELECT cantidad FROM stock_bodega WHERE producto_id=? AND bodega='Principal'",
        (producto,),
    ).fetchone()[0]
    destino = conn.execute(
        "SELECT cantidad FROM stock_bodega WHERE producto_id=? AND bodega='BARRA'",
        (producto,),
    ).fetchone()[0]
    conn.close()
    assert float(origen) == 7
    assert float(destino) == 3


def test_el_combo_exige_productos_y_se_puede_apagar(base):
    conn = base.get_connection()
    cur = conn.cursor()
    a = insertar_producto(cur, "CMB-A", "Cerveza", stock=8)
    b = insertar_producto(cur, "CMB-B", "Limón", stock=8)
    conn.commit()
    conn.close()
    with pytest.raises(ValueError):
        base.guardar_combo("X", 10, [(a, 1)])
    combo = base.guardar_combo("Cubetazo", 350, [(a, 6), (b, 1)])
    assert any(fila[0] == combo for fila in base.listar_combos_venta())
    base.desactivar_combo(combo)
    assert all(fila[0] != combo for fila in base.listar_combos_venta())


def test_solo_efectivo_tarjeta_y_transferencia_son_metodos(base):
    with pytest.raises(ValueError):
        base.guardar_metodo_pago("cheque", "Cheque", False)
    base.guardar_metodo_pago("transferencia", "Banco", True)
    assert "transferencia" in base.codigos_afectan_caja()
    assert "efectivo" in base.codigos_afectan_caja()
