import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

from accounts_manager import is_valid_email, sanitize_folder_name
from demo_seed import _line_itbis

_ruta_bot = Path(__file__).resolve().parents[1] / "badoo_bot" / "accounts_manager.py"
_spec = importlib.util.spec_from_file_location("cuentas_badoo", _ruta_bot)
_cuentas_bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cuentas_bot)
from kardex_manager import _fmt_fecha_mov, _rango_desde_combo
from movimiento_inventario_dialogs import _parse_float
from reporte_facturas_historial_manager import _fmt_fecha_corta
from seed_flujos_facturacion import _line_itbis as itbis_flujo
from styles import Styles


def test_el_itbis_de_la_semilla_es_el_dieciocho_por_ciento():
    assert _line_itbis(2, 100) == (200.0, 36.0, 236.0)
    assert itbis_flujo(1, 50, desc=10, aplica=False) == (40.0, 0.0, 40.0)


def test_las_fechas_de_kardex_e_historial():
    assert _fmt_fecha_mov("2026-11-06 14:00:00") == "06/11/2026"
    assert _fmt_fecha_mov(None) == ""
    assert _fmt_fecha_corta("2026-11-06 14:30:00") == "06/11/2026 14:30"
    hoy = datetime.now().date()
    desde, hasta = _rango_desde_combo("Últimos 7 días")
    assert hasta == hoy.isoformat()
    assert desde == (hoy - timedelta(days=7)).isoformat()
    assert _rango_desde_combo("Todos") == (None, None)
    assert _rango_desde_combo("Este mes")[0].endswith("-01")


def test_el_movimiento_lee_la_coma_como_decimal():
    assert _parse_float("12,5") == 12.5
    assert _parse_float("x", 0) == 0
    assert _parse_float("", None) is None


def test_los_colores_del_tema_estan_definidos():
    assert Styles.PRIMARY.startswith("#")
    assert Styles.DANGER != Styles.SECONDARY


def test_el_correo_de_cuentas_se_valida_y_la_carpeta_es_segura():
    assert is_valid_email("caja@ejemplo.com") is True
    assert is_valid_email("no-es-correo") is False
    assert sanitize_folder_name("Caja@Ejemplo.com") == "caja_ejemplo_com"
    assert _cuentas_bot.is_valid_email("a@b.co") is True
    assert _cuentas_bot.sanitize_folder_name("A B@c.com") == "a_b_c_com"
