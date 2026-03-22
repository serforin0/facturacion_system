"""
Persistencia de la apariencia del panel de inicio (colores, textos, módulos).
"""
import json
import os
from copy import deepcopy

from app_paths import data_directory, is_frozen

_CONFIG_FILENAME = "dashboard_config.json"


def _config_path() -> str:
    if is_frozen():
        return os.path.join(data_directory(), _CONFIG_FILENAME)
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, _CONFIG_FILENAME)


def default_config() -> dict:
    # Estilo inspirado en paneles clásicos tipo ERP: fondo gris, tarjetas claras, texto azul-violeta.
    return {
        "background_color": "#64748B",
        "header_text_color": "#F8FAFC",
        "card_color": "#FFFFFF",
        "card_hover_color": "#F1F5F9",
        "card_text_color": "#312E81",
        "card_border_color": "#E2E8F0",
        "card_border_width": 1,
        "empty_slot_border_color": "#94A3B8",
        "accent_color": "#4F46E5",
        "company_name": "Esquina Tropical",
        "company_subtitle": "Sistema de gestión\nCalle / dirección · Teléfono · Contacto",
        "footer_hint": "Pulse el botón del módulo que desea abrir",
        "footer_uppercase": True,
        "show_hostname": True,
        "grid_columns": 3,
        "grid_rows": 4,
        "modules": [
            {
                "id": "facturacion",
                "label": "Facturación",
                "icon": "🧾",
                "image_path": "",
                "action": "facturacion",
                "require_role": "",
                "visible": True,
                "slot": 0,
            },
            {
                "id": "inventario",
                "label": "Inventario",
                "icon": "📦",
                "image_path": "",
                "action": "inventario",
                "require_role": "admin",
                "visible": True,
                "slot": 1,
            },
            {
                "id": "kardex",
                "label": "Kardex",
                "icon": "📑",
                "image_path": "",
                "action": "kardex",
                "require_role": "admin",
                "visible": True,
                "slot": 8,
            },
            {
                "id": "reportes",
                "label": "Reportes",
                "icon": "📊",
                "image_path": "",
                "action": "reportes",
                "require_role": "",
                "visible": True,
                "slot": 2,
            },
            {
                "id": "caja",
                "label": "Caja",
                "icon": "💰",
                "image_path": "",
                "action": "caja",
                "require_role": "",
                "visible": True,
                "slot": 3,
            },
            {
                "id": "historial",
                "label": "Historial de facturas",
                "icon": "📜",
                "image_path": "",
                "action": "historial_facturas",
                "require_role": "",
                "visible": True,
                "slot": 4,
            },
            {
                "id": "usuarios",
                "label": "Usuarios",
                "icon": "👤",
                "image_path": "",
                "action": "usuarios",
                "require_role": "admin",
                "visible": True,
                "slot": 5,
            },
            {
                "id": "impresora",
                "label": "Impresora",
                "icon": "🖨️",
                "image_path": "",
                "action": "printer",
                "require_role": "",
                "visible": True,
                "slot": 6,
            },
            {
                "id": "indicadores",
                "label": "Indicadores",
                "icon": "📈",
                "image_path": "",
                "action": "indicadores",
                "require_role": "",
                "visible": True,
                "slot": 7,
            },
        ],
    }


def load_config() -> dict:
    path = _config_path()
    if not os.path.isfile(path):
        cfg = default_config()
        _normalize_slots(cfg)
        save_config(cfg)
        return deepcopy(cfg)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        cfg = deepcopy(default_config())
        _normalize_slots(cfg)
        return cfg

    base = default_config()
    if not isinstance(data, dict):
        cfg = deepcopy(base)
        _normalize_slots(cfg)
        return cfg

    for key in (
        "background_color",
        "header_text_color",
        "card_color",
        "card_hover_color",
        "card_text_color",
        "card_border_color",
        "card_border_width",
        "empty_slot_border_color",
        "accent_color",
        "company_name",
        "company_subtitle",
        "footer_hint",
        "footer_uppercase",
        "show_hostname",
        "grid_columns",
        "grid_rows",
    ):
        if key in data and data[key] is not None:
            base[key] = data[key]

    if isinstance(data.get("modules"), list) and data["modules"]:
        merged = []
        defaults_by_id = {m["id"]: m for m in base["modules"]}
        for item in data["modules"]:
            if not isinstance(item, dict) or "id" not in item:
                continue
            mid = item["id"]
            dflt = defaults_by_id.get(mid, {})
            row = {**dflt, **{k: v for k, v in item.items() if v is not None}}
            if "visible" not in row:
                row["visible"] = True
            merged.append(row)
        seen = {m["id"] for m in merged}
        for m in base["modules"]:
            if m["id"] not in seen:
                merged.append(deepcopy(m))
        base["modules"] = merged

    _normalize_slots(base)
    return base


def _slot_count(cfg: dict) -> int:
    try:
        cols = int(cfg.get("grid_columns") or 3)
        rows = int(cfg.get("grid_rows") or 4)
    except (TypeError, ValueError):
        cols, rows = 3, 4
    cols = max(1, min(cols, 6))
    rows = max(1, min(rows, 8))
    return cols * rows


def _normalize_slots(cfg: dict) -> None:
    """Asigna huecos 0..N-1 sin solapes; respeta slot entero si es válido."""
    n = _slot_count(cfg)
    modules = cfg.get("modules") or []
    slots_map: dict[int, dict] = {}
    unassigned: list[dict] = []

    for m in modules:
        raw = m.get("slot")
        si = None
        if raw is not None and str(raw).strip() != "":
            try:
                si = int(raw)
            except (TypeError, ValueError):
                si = None
        if si is not None and 0 <= si < n and si not in slots_map:
            slots_map[si] = m
        else:
            unassigned.append(m)

    used = set(slots_map.keys())
    for m in unassigned:
        idx = 0
        while idx < n and idx in used:
            idx += 1
        if idx < n:
            slots_map[idx] = m
            m["slot"] = idx
            used.add(idx)
        else:
            m["slot"] = None

    for i in range(n):
        if i not in slots_map:
            continue
        slots_map[i]["slot"] = i


def save_config(cfg: dict) -> None:
    path = _config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
