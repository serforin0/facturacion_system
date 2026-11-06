from datetime import datetime
from decimal import Decimal

import pytest

from ecf import dgii
from lxml import etree

from ecf.dgii import (
    DatosECF,
    LineaECF,
    armar_encf,
    calcular_totales,
    construir_ecf,
    estado_de_respuesta,
    fecha_dgii,
    recortar,
    solo_digitos,
    tipo_ecf_de,
    txt_monto,
    url_qr,
    validar,
)
from ecf.servicio import config, ruta_certificado


def _datos():
    return DatosECF(
        tipo=32,
        encf="E320000000001",
        fecha_emision="06-11-2026",
        rnc_emisor="131415161",
        razon_social_emisor="Esquina Tropical",
        direccion_emisor="Santo Domingo",
        lineas=[LineaECF("Cerveza", 2, 100)],
        pagos=[("efectivo", 236)],
        numero_interno="0000000001",
    )


def test_el_encf_y_los_textos_siguen_el_formato_dgii():
    assert armar_encf(32, 7) == "E320000000007"
    assert tipo_ecf_de("Credito_Fiscal") == 31
    assert tipo_ecf_de("otro") is None
    assert fecha_dgii("2026-11-06 10:00:00") == "06-11-2026"
    assert fecha_dgii(datetime(2026, 11, 6)) == "06-11-2026"
    assert solo_digitos("RNC 1-31-41516-1") == "131415161"
    assert recortar("  hola   mundo  ", 8) == "hola mun"
    assert txt_monto("1.005") == "1.01"


def test_los_totales_gravan_dieciocho_y_el_precio_incluido_no_se_duplica():
    tot = calcular_totales(_datos())
    assert tot.gravado == Decimal("200.00")
    assert tot.itbis == Decimal("36.00")
    assert tot.total == Decimal("236.00")
    con_itbis = _datos()
    con_itbis.precios_incluyen_itbis = True
    con_itbis.lineas = [LineaECF("Cerveza", 1, 118)]
    incluido = calcular_totales(con_itbis)
    assert incluido.gravado == Decimal("100.00")
    assert incluido.total == Decimal("118.00")


def test_el_xml_de_consumo_lleva_el_encf_y_pasa_el_esquema():
    raiz = construir_ecf(_datos(), "06-11-2026 10:00:00")
    assert raiz.findtext(".//eNCF") == "E320000000001"
    assert raiz.findtext(".//MontoTotal") == "236.00"
    etree.SubElement(raiz, "Signature")
    assert validar(raiz, 32) == []


def test_la_respuesta_de_la_dgii_se_normaliza():
    assert estado_de_respuesta({"estado": "Aceptado condicional"}) == "aceptado_condicional"
    assert estado_de_respuesta({"estado": "Aceptado"}) == "aceptado"
    assert estado_de_respuesta({"estado": "Rechazado"}) == "rechazado"
    assert estado_de_respuesta({"trackId": "abc"}) == "en_proceso"
    assert estado_de_respuesta({"http": 500}) == "error"
    with pytest.raises(ValueError):
        dgii.ClienteDGII("produccion", None)


def test_el_qr_de_consumo_menor_usa_el_timbre_fc():
    url = url_qr("testecf", _datos(), Decimal("236.00"), "06-11-2026 10:00:00", "ABCDEF")
    assert url.startswith("https://fc.dgii.gov.do/testecf/ConsultaTimbreFC")
    assert "ENCF=E320000000001" in url


def test_la_configuracion_ecf_sale_de_la_base(base):
    base.set_config("ecf_activo", "1")
    base.set_config("ecf_ambiente", "certecf")
    base.set_config("empresa_rnc", "1-31-41516-1")
    datos = config(base)
    assert datos["activo"] is True
    assert datos["ambiente"] == "certecf"
    assert datos["rnc"] == "131415161"
    assert datos["tiene_certificado"] is False
    assert ruta_certificado(base).name == "emisor.p12"
