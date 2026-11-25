"""Registro de la extensión .torrent y del protocolo magnet: en Windows.

Todo se escribe en HKEY_CURRENT_USER\\Software\\Classes, así que no hacen falta
permisos de administrador. Al desactivar se restauran los valores anteriores.

Nota: en Windows 10/11, si el usuario eligió otra app con «Abrir siempre con»,
Windows prioriza esa elección (clave UserChoice, protegida por hash). En ese caso
hay que elegir Corriente una vez en Configuración → Aplicaciones predeterminadas.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # no Windows
    winreg = None

PROGID = "Corriente.torrent"
BACKUP_KEY = r"Software\Corriente\Backup"
CLASSES = r"Software\Classes"


def available() -> bool:
    return winreg is not None and sys.platform == "win32"


def launch_command() -> tuple[str, str]:
    """(comando para abrir, ruta del icono)."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return f'"{exe}" "%1"', f'"{exe}",0'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(sys.executable)
    main = Path(__file__).resolve().with_name("main.py")
    return f'"{pyw}" "{main}" "%1"', f'"{pyw}",0'


def _set(root, path: str, name: str | None, value: str, kind=None) -> None:
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, name, 0, kind if kind is not None else winreg.REG_SZ, value)


def _get(root, path: str, name: str | None = None) -> str | None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _delete_tree(root, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, f"{path}\\{sub}")
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def _notify_shell() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def is_registered() -> bool:
    if not available():
        return False
    cmd, _ = launch_command()
    return (_get(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\.torrent") == PROGID
            and _get(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{PROGID}\\shell\\open\\command") == cmd)


def register() -> None:
    """Asocia .torrent y magnet: con esta aplicación. Lanza OSError si falla."""
    if not available():
        raise OSError("La asociación de archivos solo está disponible en Windows.")
    hk = winreg.HKEY_CURRENT_USER
    cmd, icon = launch_command()

    # Copia de seguridad de lo que hubiera antes (solo la primera vez)
    if _get(hk, BACKUP_KEY, "saved") is None:
        _set(hk, BACKUP_KEY, "torrent_progid", _get(hk, f"{CLASSES}\\.torrent") or "")
        _set(hk, BACKUP_KEY, "magnet_command", _get(hk, f"{CLASSES}\\magnet\\shell\\open\\command") or "")
        _set(hk, BACKUP_KEY, "saved", "1")

    # ProgID
    _set(hk, f"{CLASSES}\\{PROGID}", None, "Archivo torrent")
    _set(hk, f"{CLASSES}\\{PROGID}\\DefaultIcon", None, icon)
    _set(hk, f"{CLASSES}\\{PROGID}\\shell\\open\\command", None, cmd)
    # Extensión
    _set(hk, f"{CLASSES}\\.torrent", None, PROGID)
    _set(hk, f"{CLASSES}\\.torrent", "Content Type", "application/x-bittorrent")
    _set(hk, f"{CLASSES}\\.torrent\\OpenWithProgids", PROGID, b"", winreg.REG_NONE)
    # Protocolo magnet:
    _set(hk, f"{CLASSES}\\magnet", None, "URL:Enlace magnet")
    _set(hk, f"{CLASSES}\\magnet", "URL Protocol", "")
    _set(hk, f"{CLASSES}\\magnet\\DefaultIcon", None, icon)
    _set(hk, f"{CLASSES}\\magnet\\shell\\open\\command", None, cmd)
    _notify_shell()


def unregister() -> None:
    """Quita la asociación y restaura lo que había antes."""
    if not available():
        return
    hk = winreg.HKEY_CURRENT_USER
    prev_progid = _get(hk, BACKUP_KEY, "torrent_progid") or ""
    prev_magnet = _get(hk, BACKUP_KEY, "magnet_command") or ""

    if _get(hk, f"{CLASSES}\\.torrent") == PROGID:
        if prev_progid and prev_progid != PROGID:
            _set(hk, f"{CLASSES}\\.torrent", None, prev_progid)
        else:
            try:
                with winreg.OpenKey(hk, f"{CLASSES}\\.torrent", 0, winreg.KEY_WRITE) as k:
                    winreg.DeleteValue(k, None)
            except OSError:
                pass
    try:
        with winreg.OpenKey(hk, f"{CLASSES}\\.torrent\\OpenWithProgids", 0, winreg.KEY_WRITE) as k:
            winreg.DeleteValue(k, PROGID)
    except OSError:
        pass
    _delete_tree(hk, f"{CLASSES}\\{PROGID}")

    our_cmd, _ = launch_command()
    current_magnet = _get(hk, f"{CLASSES}\\magnet\\shell\\open\\command")
    if current_magnet == our_cmd:
        if prev_magnet:
            _set(hk, f"{CLASSES}\\magnet\\shell\\open\\command", None, prev_magnet)
        else:
            _delete_tree(hk, f"{CLASSES}\\magnet")
    _delete_tree(hk, BACKUP_KEY)
    _notify_shell()
