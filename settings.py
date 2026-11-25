"""Configuración persistente en JSON para Corriente.

Los ajustes se guardan en %APPDATA%\\Corriente\\settings.json. Cualquier clave
que falte en el archivo se rellena con su valor por defecto, de modo que añadir
ajustes nuevos en futuras versiones no rompe configuraciones antiguas.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any

APP_NAME = "Corriente"
APP_ID = "Corriente.BitTorrent"

PORT_MIN = 49152
PORT_MAX = 65535

DEFAULT_PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.theoks.net:6969/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://opentracker.io:6969/announce",
    "https://tracker.tamersunion.org:443/announce",
]


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def resume_dir() -> Path:
    path = data_dir() / "resume"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> str:
    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.exists() else Path.home())


def random_port() -> int:
    return random.randint(PORT_MIN, PORT_MAX)


DEFAULTS: dict[str, Any] = {
    # General
    "theme": "oscuro",                 # "oscuro" | "claro"
    "download_dir": "",                # se rellena en la primera ejecución
    "ask_on_add": True,                # mostrar diálogo al añadir torrents
    "start_paused": False,
    "close_to_tray": True,
    "minimize_to_tray": True,
    "start_minimized": False,
    "first_run_done": False,
    "tray_hint_shown": False,
    "file_association": False,         # .torrent y magnet: registrados

    # Conexión
    "listen_port": 0,                  # 0 => se genera aleatorio al arrancar
    "network_interface": "",           # "" = todas; si no, nombre del adaptador (p. ej. "Ethernet")
    "upnp": True,
    "natpmp": True,
    "dht": True,
    "pex": True,
    "lsd": True,
    "encryption": "habilitado",        # "deshabilitado" | "habilitado" | "forzado"
    "max_connections": 800,
    "max_peers_per_torrent": 150,
    "upload_slots_per_torrent": 4,

    # Velocidad
    "upload_capacity_kbps": 0,         # subida real de la conexión en KiB/s (0 = desconocida)
    "upload_percent": 80,              # % de la capacidad que se usa como límite
    "download_limit_kbps": 0,          # 0 = ilimitado

    # Cola
    "active_limit": 5,
    "active_downloads": 3,
    "active_seeds": 5,

    # Disco
    "preallocate": False,              # False = archivos dispersos (sparse)
    "disk_threads": 8,
    "hashing_threads": 2,
    "file_pool_size": 500,
    "send_buffer_mb": 4,

    # Trackers
    "add_public_trackers": False,
    "public_trackers": DEFAULT_PUBLIC_TRACKERS,

    # Ventana
    "window_geometry": "",
    "window_state": "",
    "splitter_state": "",
    "header_state": "",
}


class Settings:
    """Diccionario de ajustes con guardado atómico a disco."""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    # -- persistencia -------------------------------------------------------
    def load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key in DEFAULTS:
                            self._data[key] = value
            except (OSError, json.JSONDecodeError) as exc:
                # Un JSON corrupto no debe impedir arrancar: se guarda copia y se sigue.
                try:
                    self.path.replace(self.path.with_suffix(".json.bak"))
                except OSError:
                    pass
                print(f"[ajustes] archivo dañado, se usan valores por defecto: {exc}", file=sys.stderr)
        self._sanitize()

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _sanitize(self) -> None:
        d = self._data
        if not d.get("download_dir"):
            d["download_dir"] = default_download_dir()
        port = int(d.get("listen_port") or 0)
        if not (PORT_MIN <= port <= PORT_MAX):
            d["listen_port"] = random_port()
        if d.get("encryption") not in ("deshabilitado", "habilitado", "forzado"):
            d["encryption"] = "habilitado"
        if d.get("theme") not in ("oscuro", "claro"):
            d["theme"] = "oscuro"
        d["upload_percent"] = max(1, min(100, int(d.get("upload_percent") or 80)))
        if not isinstance(d.get("public_trackers"), list):
            d["public_trackers"] = list(DEFAULT_PUBLIC_TRACKERS)

    # -- acceso -------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)
        self._sanitize()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # -- valores derivados ----------------------------------------------------
    def effective_upload_limit_bytes(self) -> int:
        """Límite de subida en bytes/s (0 = ilimitado)."""
        cap = float(self.get("upload_capacity_kbps") or 0)
        if cap <= 0:
            return 0
        return int(cap * 1024 * int(self.get("upload_percent")) / 100)

    def download_limit_bytes(self) -> int:
        return int(float(self.get("download_limit_kbps") or 0) * 1024)
