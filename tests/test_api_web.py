"""Flujos HTTP del mostrador: login, caja, venta, historial y permisos."""
import contextlib
import io

import pytest
from fastapi.testclient import TestClient

import web_server
from database import Database
from tests.conftest import insertar_producto


@pytest.fixture
def api(tmp_path, monkeypatch):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        base = Database(str(tmp_path / "web.db"))
    monkeypatch.setattr(web_server, "db", lambda: base)
    cliente = TestClient(web_server.app)
    cliente.base = base
    return cliente


def _entrar(api, usuario="admin", clave="admin07!"):
    resp = api.post("/api/login", json={"username": usuario, "password": clave})
    assert resp.status_code == 200, resp.text
    assert resp.json()["usuario"] == usuario
    return resp


def _producto(api, codigo="WEB-1", precio=100, stock=8):
    conn = api.base.get_connection()
    cur = conn.cursor()
    pid = insertar_producto(cur, codigo, "Agua de prueba", stock=stock, precio=precio)
    conn.commit()
    conn.close()
    return pid


def test_las_paginas_del_mostrador_responden(api):
    for ruta in ("/", "/tablet", "/cocina", "/pantalla", "/sw.js"):
        resp = api.get(ruta)
        assert resp.status_code == 200, ruta
    html = api.get("/").text
    assert "MD Alliance" in html
    assert "Entrada del mesero" in html
    assert "delete g.opacity" in html
    assert 'scope="col"' in html
    assert "oculto-visual\">Acciones" in html
    assert 'id="invCod"' in html
    assert "Escanea o escribe" in html
    assert "function lecturaDeLector" in html
    assert "function lecturaEnProducto" in html
    assert 'id="vendiendo"' in html
    assert "0x1b, 0x70, 0x00, 0x19, 0xfa" in html
    assert "abrirCajon(); ver('caja')" in html
    assert "Venta al por mayor" in html


def test_sin_sesion_no_hay_estado_ni_venta(api):
    assert api.get("/api/estado").status_code == 401
    assert api.post("/api/vender", json={"items": [{"id": 1, "cantidad": 1}]}).status_code == 401
    malo = api.post("/api/login", json={"username": "admin", "password": "no"})
    assert malo.status_code == 401


def test_el_admin_abre_caja_vende_y_el_historial_lo_ve(api):
    _entrar(api)
    estado = api.get("/api/estado")
    assert estado.status_code == 200
    assert estado.json()["usuario"] == "admin"
    assert estado.json()["caja"] is None
    assert "mostrador" in estado.json()["permisos"]

    pid = _producto(api)
    catalogo = api.get("/api/catalogo", params={"q": "Agua de prueba"})
    assert catalogo.status_code == 200
    assert any(p["id"] == pid for p in catalogo.json()["productos"])

    pid_barra = _producto(api, codigo="7501234567890", precio=50, stock=3)
    por_codigo = api.get("/api/catalogo", params={"q": "7501234567890", "categoria": 99999})
    assert por_codigo.status_code == 200
    barras = por_codigo.json()["productos"]
    assert any(p["id"] == pid_barra and p["codigo"] == "7501234567890" for p in barras)

    vacia = api.post("/api/vender", json={"items": [{"id": pid, "cantidad": 1}], "efectivo": 118})
    assert vacia.status_code == 400
    assert "caja" in vacia.json()["detail"].lower()

    abierta = api.post("/api/caja/abrir", json={"nombre": "Caja web", "fondo": 200})
    assert abierta.status_code == 200
    assert api.get("/api/estado").json()["caja"]["fondo"] == 200
    repetida = api.post("/api/caja/abrir", json={"nombre": "Otra", "fondo": 0})
    assert repetida.status_code == 400

    venta = api.post(
        "/api/vender",
        json={
            "items": [{"id": pid, "cantidad": 2, "nivel": 1}],
            "efectivo": 300,
            "id_local": "local-1",
            "observaciones": "mesa 4",
        },
    )
    assert venta.status_code == 200, venta.text
    cuerpo = venta.json()
    assert cuerpo["ok"] is True
    assert cuerpo["subtotal"] == 200
    assert cuerpo["itbis"] == 36
    assert cuerpo["total"] == 236
    assert cuerpo["cambio"] == 64
    assert cuerpo["numero"].startswith("F-")

    otra = api.post(
        "/api/vender",
        json={"items": [{"id": pid, "cantidad": 1}], "efectivo": 118, "id_local": "local-1"},
    )
    assert otra.status_code == 200
    assert otra.json()["id"] == cuerpo["id"]

    conn = api.base.get_connection()
    stock = conn.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert float(stock) == 6

    historial = api.get("/api/historial")
    assert historial.status_code == 200
    assert any(item["numero"] == cuerpo["numero"] for item in historial.json()["items"])

    factura = api.get(f"/api/factura/{cuerpo['id']}")
    assert factura.status_code == 200
    assert factura.json()["total"] == 236

    reportes = api.get("/api/reportes", params={"dias": 1})
    assert reportes.status_code == 200
    assert any(v["nombre"] == "Agua de prueba" for v in reportes.json()["vendidos"])


def test_el_mesero_no_abre_caja_y_el_cajero_si_crea_cliente(api):
    _entrar(api)
    creado = api.post(
        "/api/usuarios",
        json={"username": "mesero1", "password": "mesa", "role": "mesero"},
    )
    assert creado.status_code == 200
    api.post("/api/logout")

    _entrar(api, "mesero1", "mesa")
    assert api.get("/api/estado").json()["rol"] == "mesero"
    caja = api.post("/api/caja/abrir", json={"nombre": "Caja", "fondo": 0})
    assert caja.status_code == 403
    api.post("/api/logout")

    _entrar(api)
    cliente = api.post("/api/clientes/rapido", json={"nombre": "Ana Pérez", "telefono": "8090000000"})
    assert cliente.status_code == 200
    assert cliente.json()["id"] > 0
    corto = api.post("/api/clientes/rapido", json={"nombre": "A"})
    assert corto.status_code == 400


def test_inventario_metodos_y_empresa_responden_al_admin(api):
    _entrar(api)
    _producto(api, "WEB-INV", precio=50, stock=3)
    inventario = api.get("/api/inventario", params={"q": "Agua de prueba"})
    assert inventario.status_code == 200
    assert inventario.json()["items"]
    metodos = api.get("/api/metodos")
    assert metodos.status_code == 200
    assert any(m["codigo"] == "efectivo" for m in metodos.json()["items"])
    empresa = api.get("/api/empresa")
    assert empresa.status_code == 200
    bodegas = api.get("/api/bodegas")
    assert bodegas.status_code == 200
    ncf = api.get("/api/ncf")
    assert ncf.status_code == 200
    indicadores = api.get("/api/indicadores")
    assert indicadores.status_code == 200
    ecf = api.get("/api/ecf/estado")
    assert ecf.status_code == 200
    assert ecf.json()["activo"] is False


def test_la_venta_al_por_mayor_baja_el_stock_del_almacen(api):
    _entrar(api, "empleado", "empreado07!")
    assert api.get("/api/mayor").status_code == 403
    api.post("/api/logout")

    _entrar(api)
    assert "mayor" in api.get("/api/estado").json()["permisos"]
    pid = _producto(api, codigo="MAY-1", precio=100, stock=20)
    conn = api.base.get_connection()
    conn.execute("UPDATE productos SET precio_2=80 WHERE id=?", (pid,))
    conn.execute(
        "INSERT INTO stock_bodega (producto_id, bodega, cantidad) VALUES (?, 'Almacén', 20)",
        (pid,),
    )
    conn.commit()
    conn.close()

    vacia = api.post("/api/mayor", json={"cliente": "Colmado", "items": []})
    assert vacia.status_code == 400

    de_mas = api.post("/api/mayor", json={"cliente": "Colmado", "items": [{"id": pid, "cantidad": 50}]})
    assert de_mas.status_code == 400

    venta = api.post("/api/mayor", json={
        "cliente": "Colmado Pérez",
        "nota": "Entrega en la mañana",
        "items": [{"id": pid, "cantidad": 4}],
    })
    assert venta.status_code == 200, venta.text
    cuerpo = venta.json()
    assert cuerpo["numero"].startswith("VM-")
    assert cuerpo["total"] == 320
    assert cuerpo["bodega"] == "Almacén"

    conn = api.base.get_connection()
    stock = conn.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()[0]
    almacen = conn.execute(
        "SELECT cantidad FROM stock_bodega WHERE producto_id=? AND bodega='Almacén'",
        (pid,),
    ).fetchone()[0]
    kardex = conn.execute(
        """
        SELECT tipo_codigo, bodega_codigo, cantidad
        FROM movimientos_inventario WHERE producto_id=? ORDER BY id DESC LIMIT 1
        """,
        (pid,),
    ).fetchone()
    conn.close()
    assert float(stock) == 16
    assert float(almacen) == 16
    assert kardex[0] == "MA"
    assert kardex[1] == "Almacén"
    assert float(kardex[2]) == -4

    lista = api.get("/api/mayor")
    assert lista.status_code == 200
    item = next(v for v in lista.json()["items"] if v["numero"] == cuerpo["numero"])
    assert item["cliente"] == "Colmado Pérez"
    assert item["lineas"][0]["cantidad"] == 4
    assert item["lineas"][0]["nombre"] == "Agua de prueba"


def test_el_empleado_publica_la_venta_y_el_cuadre_la_recibe(api):
    _entrar(api, "empleado", "empreado07!")
    pid = _producto(api, "SYNC-1", precio=25, stock=10)
    resp = api.post("/api/sync/venta", json={
        "ventas": [{
            "numero": "F-ESC-1",
            "fecha": "2026-11-10 11:00:00",
            "subtotal": 50,
            "itbis": 0,
            "total": 50,
            "usuario": "empleado",
            "lineas": [{
                "codigo": "SYNC-1",
                "nombre": "Agua de prueba",
                "cantidad": 2,
                "precio": 25,
                "total": 50,
            }],
            "pagos": [{"tipo": "efectivo", "monto": 50}],
        }],
    })
    assert resp.status_code == 200, resp.text
    conn = api.base.get_connection()
    stock = conn.execute("SELECT stock FROM productos WHERE id=?", (pid,)).fetchone()[0]
    cuadre = conn.execute(
        """
        SELECT SUM(p.monto)
        FROM pagos_factura p
        JOIN facturas f ON f.id = p.factura_id
        JOIN cierres_caja c ON c.id = p.cierre_id
        WHERE f.numero='F-ESC-1' AND c.fecha_cierre IS NULL
        """
    ).fetchone()[0]
    conn.close()
    assert float(stock) == 8
    assert float(cuadre) == 50
