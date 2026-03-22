# -*- mode: python ; coding: utf-8 -*-
# PyInstaller — generar .exe en Windows:  pyinstaller --noconfirm main.spec
# (Desde macOS/Linux no se puede producir un .exe; use GitHub Actions o una PC Windows.)

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

try:
    SPEC  # type: ignore[name-defined]
    _spec_dir = os.path.dirname(os.path.abspath(SPEC))
except NameError:
    _spec_dir = os.path.dirname(os.path.abspath("."))

_datas = []
_datas += collect_data_files("customtkinter")
_datas += collect_data_files("matplotlib")

_assets = os.path.join(_spec_dir, "assets")
if os.path.isdir(_assets):
    _datas.append((_assets, "assets"))

_binaries = []
try:
    _binaries += collect_dynamic_libs("pymupdf")
except Exception:
    pass

_hidden = [
    "tkinterdnd2",
    "fitz",
    "matplotlib.backends.backend_tkagg",
    "PIL._tkinter_finder",
]

_icon = os.path.join(_spec_dir, "assets", "icono.ico")
_icon_arg = _icon if os.path.isfile(_icon) else None

a = Analysis(
    ["main.py"],
    pathex=[_spec_dir],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SistemaFacturacion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_arg,
)
