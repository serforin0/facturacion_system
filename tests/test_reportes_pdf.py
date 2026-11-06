from report_pdf_builder import _cabecera_empresa, _rd, _xml, build_cuadre_caja_pdf


def test_el_dinero_y_la_cabecera_escapan_el_texto():
    assert _rd(1500) == "RD$ 1,500.00"
    assert _rd(None) == "RD$ 0.00"
    assert _xml("A & B <C>") == "A &amp; B &lt;C&gt;"
    cabecera = _cabecera_empresa(
        {"nombre": "Esquina & Tropical", "direccion": "Calle 1", "telefono": "809", "rnc": "131"}
    )
    assert "Esquina &amp; Tropical" in cabecera
    assert "Tel. 809" in cabecera
    assert "RNC 131" in cabecera


def test_el_cuadre_cerrado_en_cero_sale_en_pdf():
    pdf = build_cuadre_caja_pdf(
        {
            "estado": "cerrado",
            "diferencia": 0,
            "contado": 500,
            "id": 4,
            "nombre": "Caja 1",
            "usuario_apertura": "admin",
            "apertura": "2026-11-06 08:00:00",
            "usuario_cierre": "admin",
            "cierre": "2026-11-06 22:00:00",
            "n_facturas": 1,
            "n_anuladas": 0,
            "ventas_total": 118,
            "fondo": 500,
            "ventas_efectivo": 0,
            "ingresos": 0,
            "retiros": 0,
            "esperado": 500,
            "pagos": [{"tipo": "efectivo", "monto": 118}],
            "movimientos": [],
            "facturas": [],
        },
        {"nombre": "Esquina Tropical", "direccion": "Santo Domingo", "telefono": "", "rnc": "131"},
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500
