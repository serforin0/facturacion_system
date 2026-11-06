from facturacion_erp_manager import (
    _fmt_fecha_factura,
    _fmt_monto,
    _fmt_numero_display,
    _fmt_vencimiento,
    _parse_fecha_entrada,
    _terminos_pago,
)


def test_las_fechas_y_el_numero_se_muestran_como_en_el_erp():
    assert _fmt_vencimiento(None) == "—"
    assert _fmt_vencimiento("2026-11-06") == "06/11/2026"
    assert _fmt_fecha_factura("2026-11-06 14:30:00") == "06/11/2026"
    assert _fmt_fecha_factura(None) == ""
    assert _fmt_numero_display("42") == "0000000042"
    assert _fmt_numero_display("B0200000001") == "0200000001"
    assert _fmt_monto(1234.5) == "1,234.50"


def test_la_fecha_de_entrada_acepta_los_tres_formatos():
    assert str(_parse_fecha_entrada("2026-11-06")) == "2026-11-06"
    assert str(_parse_fecha_entrada("06/11/2026")) == "2026-11-06"
    assert str(_parse_fecha_entrada("06-11-2026")) == "2026-11-06"
    assert _parse_fecha_entrada("") is None
    assert _parse_fecha_entrada("ayer") is None


def test_el_saldo_pendiente_marca_credito():
    assert _terminos_pago(100, 100) == "En Efectivo"
    assert _terminos_pago(100, 40) == "Pendiente / crédito"
