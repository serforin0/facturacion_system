import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from cuenta_servidor import SERVIDOR_POR_DEFECTO, ServidorInalcanzable, consultar_usuario, url_servidor


class _Respuesta:
    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_la_url_del_servidor_usa_la_guardada_o_la_de_defecto(base):
    assert url_servidor(None) == SERVIDOR_POR_DEFECTO
    base.set_config("servidor_facturacion", "https://caja.ejemplo.com/")
    assert url_servidor(base) == "https://caja.ejemplo.com"


def test_consultar_usuario_devuelve_el_rol_o_nada(monkeypatch):
    def ok(req, timeout):
        return _Respuesta(json.dumps({"rol": "admin"}).encode())

    monkeypatch.setattr("cuenta_servidor.urllib.request.urlopen", ok)
    assert consultar_usuario("https://caja.ejemplo.com", "admin", "x") == "admin"

    def sin_rol(req, timeout):
        return _Respuesta(b"{}")

    monkeypatch.setattr("cuenta_servidor.urllib.request.urlopen", sin_rol)
    assert consultar_usuario("https://caja.ejemplo.com", "admin", "x") is None


def test_un_401_no_es_caida_y_un_500_si(monkeypatch):
    def no_autorizado(req, timeout):
        raise HTTPError(req.full_url, 401, "no", hdrs=None, fp=BytesIO(b""))

    monkeypatch.setattr("cuenta_servidor.urllib.request.urlopen", no_autorizado)
    assert consultar_usuario("https://caja.ejemplo.com", "admin", "mala") is None

    def caido(req, timeout):
        raise URLError("sin red")

    monkeypatch.setattr("cuenta_servidor.urllib.request.urlopen", caido)
    with pytest.raises(ServidorInalcanzable):
        consultar_usuario("https://caja.ejemplo.com", "admin", "x")
