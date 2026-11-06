from caja_manager import calcular_totales_desde_apertura, efectivo_esperado_turno
from facturacion_erp_manager import _texto_cierre_caja_turno


def test_el_efectivo_esperado_suma_fondo_ventas_e_ingresos():
    assert efectivo_esperado_turno(1000, 200, 50, 30) == 1220
    assert efectivo_esperado_turno(None, None, None, None) == 0


def test_el_turno_separa_efectivo_tarjeta_y_otros(base):
    conn = base.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO facturas (
            numero, fecha, tipo_comprobante, subtotal, descuento_total, impuesto_total,
            total, estado, usuario, cierre_id
        ) VALUES ('T-CAJA-1', '2099-06-01 10:00:00', 'consumidor_final', 150, 0, 0, 150, 'emitida', 'caja', 99999)
        """
    )
    factura = cur.lastrowid
    cur.executemany(
        "INSERT INTO pagos_factura (factura_id, tipo_pago, monto) VALUES (?,?,?)",
        [(factura, "efectivo", 100), (factura, "tarjeta", 40), (factura, "transferencia", 10)],
    )
    conn.commit()
    conn.close()
    total, efectivo, tarjeta, otros = calcular_totales_desde_apertura(base, "2099-01-01", 99999)
    assert total == 150
    assert efectivo == 100
    assert tarjeta == 40
    assert otros == 10


def test_el_texto_de_cierre_cuadra_fondo_y_contado(base):
    fila = (1, "Caja 1", "2099-06-01 08:00:00", None, "admin", None, 500, 0, 0, 0, 0, 0, 0, 0, None)
    texto = _texto_cierre_caja_turno(base, fila, "Yordy", 500, "06/11/2026")
    assert "Caja: Caja 1" in texto
    assert "Operador: Yordy" in texto
    assert "RD$ 500.00" in texto
    assert "Diferencia (contado − esperado):             RD$ 0.00" in texto
