"""Pantalla completa del programa de escritorio en la computadora del mostrador."""
from __future__ import annotations

import os
import sys

from app_paths import data_directory

MARCA = "kiosco.flag"
SALIDA = "kiosco.salir"

_gancho = None
_callback = None
_pedir_salida = False


def modo_kiosco(argv: list[str] | None = None, plataforma: str | None = None, carpeta: str | None = None) -> bool:
    """Solo Windows, y solo si esa computadora se instaló como mostrador fijo."""
    if (plataforma or sys.platform) != "win32":
        return False
    if "--kiosco" in (argv if argv is not None else sys.argv):
        return True
    return os.path.isfile(os.path.join(carpeta or data_directory(), MARCA))


def tecla_bloqueada(vk: int, alt: bool, ctrl: bool, shift: bool) -> str:
    """'salir' abre el escritorio al técnico. 'bloquear' se traga la tecla. '' sigue de largo."""
    if ctrl and shift and vk == 0x7B:
        return "salir"
    if vk in (0x5B, 0x5C) or vk == 0x7A:
        return "bloquear"
    if alt and vk in (0x09, 0x73, 0x1B, 0x20):
        return "bloquear"
    if ctrl and shift and vk == 0x1B:
        return "bloquear"
    return ""


def marcar_salida_tecnico(carpeta: str | None = None) -> str:
    ruta = os.path.join(carpeta or data_directory(), SALIDA)
    with open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write("tecnico\n")
        archivo.flush()
        os.fsync(archivo.fileno())
    return ruta


def salida_pedida() -> bool:
    return _pedir_salida


def _al_pulsar(vk: int, alt: bool, ctrl: bool, shift: bool) -> bool:
    global _pedir_salida
    accion = tecla_bloqueada(vk, alt, ctrl, shift)
    if accion == "salir":
        _pedir_salida = True
        return True
    return accion == "bloquear"


def instalar_gancho() -> None:
    """Traga la tecla Windows, Alt+Tab y Alt+F4 mientras el programa está abierto."""
    global _gancho, _callback
    if sys.platform != "win32" or _gancho:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    def hook(nCode, wParam, lParam):
        if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            alt = bool(info.flags & 0x20) or bool(user32.GetAsyncKeyState(0x12) & 0x8000)
            ctrl = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
            shift = bool(user32.GetAsyncKeyState(0x10) & 0x8000)
            if _al_pulsar(int(info.vkCode), alt, ctrl, shift):
                return 1
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    _callback = HOOKPROC(hook)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    _gancho = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _callback, None, 0)


def _vigilar(root, al_salir) -> None:
    if not root.winfo_exists():
        return
    if _pedir_salida:
        al_salir()
        return
    root.after(200, lambda: _vigilar(root, al_salir))


def _pantalla_completa(root) -> None:
    root.attributes("-fullscreen", True)
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)


def activar_kiosco(root, al_salir) -> None:
    _pantalla_completa(root)
    root.bind_all("<Alt-F4>", lambda _evento: "break")
    instalar_gancho()
    root.after(100, lambda: _pantalla_completa(root))
    root.after(200, lambda: _vigilar(root, al_salir))
