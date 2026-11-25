"""Motor BitTorrent basado en libtorrent 2.x.

Este módulo no depende de Qt: la interfaz llama a `poll()` y `refresh()`
periódicamente (desde un QTimer) y recibe eventos como tuplas sencillas.
Cada torrent se identifica por una *clave*: el info-hash v1 en hexadecimal
(o el v2 si el torrent es solo v2).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import libtorrent as lt

from settings import APP_NAME, Settings, resume_dir

HISTORY_LEN = 600  # segundos de historial de velocidad (10 min)

VPN_HINTS = ("vpn", "tap-", "tap ", "tun", "wireguard", "wintun", "nordlynx", "openvpn",
             "proton", "mullvad", "surfshark", "expressvpn", "windscribe", "cisco anyconnect",
             "fortinet", "zerotier", "tailscale", "hamachi", "pia ", "private internet access")


class TorrentError(Exception):
    """Error con un mensaje ya listo para mostrar al usuario."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def key_from_ih(ih: "lt.info_hash_t") -> str:
    return str(ih.v1) if ih.has_v1() else str(ih.v2)


def key_of(handle: "lt.torrent_handle") -> str:
    return key_from_ih(handle.info_hashes())


# Códigos de error de Windows y errno de C relevantes
_DISK_FULL = {112, 39, 28}           # ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL, ENOSPC
_ACCESS = {5, 13, 1}                 # ERROR_ACCESS_DENIED, EACCES, EPERM
_NOT_FOUND = {2, 3}                  # archivo / ruta no encontrados
_IN_USE = {32, 33}                   # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_PATH_TOO_LONG = {206, 36}           # ERROR_FILENAME_EXCED_RANGE, ENAMETOOLONG


def explain_error(ec: Any, filename: str = "") -> str:
    """Convierte un error_code de libtorrent en un mensaje claro en español."""
    try:
        value = ec.value()
        msg = ec.message()
    except AttributeError:
        return str(ec)
    where = f" ({filename})" if filename else ""
    if value in _DISK_FULL or "space" in msg.lower():
        return f"Disco lleno: no queda espacio en la unidad de destino{where}. Libera espacio o mueve la descarga a otra unidad."
    if value in _ACCESS:
        return f"Permiso denegado{where}. Elige una carpeta en la que tu usuario pueda escribir (p. ej. Descargas) o revisa el antivirus."
    if value in _IN_USE:
        return f"El archivo está en uso por otro programa{where}. Ciérralo y vuelve a reanudar el torrent."
    if value in _PATH_TOO_LONG:
        return f"La ruta es demasiado larga para Windows{where}. Usa una carpeta de destino más corta."
    if value in _NOT_FOUND:
        return f"No se encuentra el archivo o la carpeta{where}. ¿Se ha desconectado la unidad o se ha borrado la carpeta?"
    return f"{msg}{where} (código {value})"


def is_vpn_interface(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in VPN_HINTS)


STATE_NAMES = {
    lt.torrent_status.states.checking_files: "Comprobando",
    lt.torrent_status.states.downloading_metadata: "Obteniendo metadatos",
    lt.torrent_status.states.downloading: "Descargando",
    lt.torrent_status.states.finished: "Completado",
    lt.torrent_status.states.seeding: "Sembrando",
    lt.torrent_status.states.checking_resume_data: "Comprobando datos",
}


@dataclass
class TorrentMeta:
    """Información propia de la app que libtorrent no guarda."""
    pending_selection: bool = False      # magnet esperando a que el usuario elija archivos
    hash_fails: int = 0
    perf_warnings: deque = field(default_factory=lambda: deque(maxlen=50))  # (t, code)
    last_error: str = ""
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LEN))  # (down, up)
    prev_state: Any = None
    added_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

class TorrentEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: lt.session | None = None
        self.handles: dict[str, lt.torrent_handle] = {}
        self.meta: dict[str, TorrentMeta] = {}
        self.status: dict[str, lt.torrent_status] = {}
        self.global_history: deque = deque(maxlen=HISTORY_LEN)
        self.global_perf: deque = deque(maxlen=100)
        self._events: list[tuple] = []
        self._outstanding_resume = 0
        self._pending_file = resume_dir() / "pendientes.json"
        self._last_resume_save = time.time()
        self._last_stats_post = 0.0

        # Diagnóstico de red
        self.listen_port = 0
        self.listen_error = ""
        self.portmap_ok = False
        self.portmap_error = ""
        self.incoming_seen = False
        self.started_at = time.time()
        self.dht_nodes = 0
        self.bind_error = ""
        self._bind: tuple[str, str, str] = ("", "", "")
        self._last_bind_check = 0.0

    # ------------------------------------------------------------------ arranque
    def start(self) -> None:
        pack = self._build_settings_pack()
        self._bind = self._bind_config()
        pack["listen_interfaces"], pack["outgoing_interfaces"] = self._bind[0], self._bind[1]
        self.bind_error = self._bind[2]
        self.session = lt.session(self._filter_pack(pack, lt.default_settings()))
        self.listen_port = int(self.settings["listen_port"])
        self._last_bind_check = time.time()
        self._load_resume_data()

    def _bind_config(self) -> tuple[str, str, str]:
        """(listen_interfaces, outgoing_interfaces, error) según la interfaz elegida.

        Con una interfaz concreta, todas las conexiones (entrantes y salientes) salen
        por sus IP. Sirve para usar Corriente fuera de una VPN (split tunneling) o,
        al revés, para obligarle a usar solo la VPN."""
        port = int(self.settings["listen_port"])
        name = self.settings["network_interface"]
        if not name:
            return f"0.0.0.0:{port},[::]:{port}", "", ""
        import netif
        ips = netif.addresses_of(name)
        if not ips:
            # La interfaz no está conectada: no se usa ninguna otra para no saltarse la elección.
            return f"127.0.0.1:{port}", "127.0.0.1", f"la interfaz «{name}» no está conectada"
        listen = ",".join(f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}" for ip in ips)
        return listen, ",".join(ips), ""

    def _apply_bind(self, force: bool = False) -> bool:
        bind = self._bind_config()
        if not force and bind == self._bind:
            return False
        self._bind = bind
        self.bind_error = bind[2]
        self.session.apply_settings({"listen_interfaces": bind[0], "outgoing_interfaces": bind[1]})
        self.listen_error = ""
        self.portmap_ok = False
        self.portmap_error = ""
        self.incoming_seen = False
        self.started_at = time.time()
        if bind[2]:
            self._events.append(("error", None, f"Sin conexión: {bind[2]}. Conéctala o elige otra en Ajustes → Conexión."))
        else:
            self._events.append(("log", f"Escuchando en {bind[0]}"))
        return True

    @staticmethod
    def _filter_pack(pack: dict, known: dict) -> dict:
        """Descarta ajustes que no existan en esta versión de libtorrent."""
        return {k: v for k, v in pack.items() if k in known}

    def _build_settings_pack(self) -> dict:
        s = self.settings
        enc = {
            "deshabilitado": int(lt.enc_policy.pe_disabled),
            "habilitado": int(lt.enc_policy.pe_enabled),
            "forzado": int(lt.enc_policy.pe_forced),
        }[s["encryption"]]
        slots = int(s["upload_slots_per_torrent"])
        mask = (lt.alert_category.error | lt.alert_category.status | lt.alert_category.storage
                | lt.alert_category.tracker | lt.alert_category.port_mapping
                | lt.alert_category.performance_warning | lt.alert_category.stats)
        return {
            "user_agent": f"{APP_NAME}/1.0 libtorrent/{lt.__version__}",
            "peer_fingerprint": "-CR1000-",
            "alert_mask": int(mask),
            "alert_queue_size": 10000,
            # Red
            "enable_upnp": bool(s["upnp"]),
            "enable_natpmp": bool(s["natpmp"]),
            "enable_dht": bool(s["dht"]),
            "enable_lsd": bool(s["lsd"]),
            "out_enc_policy": enc,
            "in_enc_policy": enc,
            "allowed_enc_level": int(lt.enc_level.pe_both),
            "prefer_rc4": False,
            "connections_limit": int(s["max_connections"]),
            "unchoke_slots_limit": max(8, slots * int(s["active_limit"])),
            "announce_to_all_tiers": True,
            "announce_to_all_trackers": True,
            "connection_speed": 50,
            # Velocidad
            "upload_rate_limit": s.effective_upload_limit_bytes(),
            "download_rate_limit": s.download_limit_bytes(),
            # Cola
            "active_limit": int(s["active_limit"]),
            "active_downloads": int(s["active_downloads"]),
            "active_seeds": int(s["active_seeds"]),
            "dont_count_slow_torrents": True,
            "auto_manage_startup": 60,
            # Disco: más hilos de E/S, búfer de envío grande y caché del SO activa
            "aio_threads": int(s["disk_threads"]),
            "hashing_threads": int(s["hashing_threads"]),
            "file_pool_size": int(s["file_pool_size"]),
            "send_buffer_watermark": int(s["send_buffer_mb"]) * 1024 * 1024,
            "send_buffer_low_watermark": 512 * 1024,
            "send_buffer_watermark_factor": 150,
            "max_queued_disk_bytes": 64 * 1024 * 1024,
            "checking_mem_usage": 2048,
            "disk_io_write_mode": 0,   # enable_os_cache
            "disk_io_read_mode": 0,
        }

    def apply_settings(self) -> None:
        """Aplica los ajustes actuales a una sesión en marcha."""
        if not self.session:
            return
        pack = self._filter_pack(self._build_settings_pack(), self.session.get_settings())
        self.session.apply_settings(pack)
        self.listen_port = int(self.settings["listen_port"])
        self._apply_bind()
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            h.set_max_connections(int(self.settings["max_peers_per_torrent"]))
            h.set_max_uploads(int(self.settings["upload_slots_per_torrent"]))
            try:
                if self.settings["pex"]:
                    h.unset_flags(lt.torrent_flags.disable_pex)
                else:
                    h.set_flags(lt.torrent_flags.disable_pex)
            except Exception:
                pass

    # ------------------------------------------------------------------ añadir
    @staticmethod
    def load_torrent_info(path: str) -> "lt.torrent_info":
        p = Path(path)
        if not p.exists():
            raise TorrentError(f"El archivo no existe:\n{path}")
        try:
            if p.stat().st_size > 64 * 1024 * 1024:
                raise TorrentError("El archivo es demasiado grande para ser un .torrent válido.")
            return lt.torrent_info(str(p))
        except TorrentError:
            raise
        except PermissionError:
            raise TorrentError(f"No hay permiso para leer el archivo:\n{path}")
        except Exception as exc:  # libtorrent lanza RuntimeError con el motivo
            raise TorrentError(f"El archivo no es un torrent válido o está dañado.\n\nDetalle: {exc}")

    @staticmethod
    def parse_magnet(uri: str) -> "lt.add_torrent_params":
        uri = uri.strip()
        if not uri.lower().startswith("magnet:?"):
            raise TorrentError("El texto no parece un enlace magnet (debe empezar por «magnet:?»).")
        try:
            params = lt.parse_magnet_uri(uri)
        except Exception as exc:
            raise TorrentError(f"Enlace magnet inválido.\n\nDetalle: {exc}")
        ih = params.info_hashes
        if not (ih.has_v1() or ih.has_v2()):
            raise TorrentError("El enlace magnet no contiene un info-hash válido.")
        return params

    def _check_dest(self, save_path: str, needed: int = 0) -> None:
        try:
            Path(save_path).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise TorrentError(f"No hay permiso para crear la carpeta de destino:\n{save_path}")
        except OSError as exc:
            raise TorrentError(f"No se puede usar la carpeta de destino:\n{save_path}\n\n{exc}")
        if not os.access(save_path, os.W_OK):
            raise TorrentError(f"No tienes permiso de escritura en:\n{save_path}")
        if needed:
            free = shutil.disk_usage(save_path).free
            if free < needed:
                raise TorrentError(
                    f"No hay espacio suficiente en el disco.\n\nNecesario: {needed / 1024**3:.2f} GB\n"
                    f"Libre: {free / 1024**3:.2f} GB")

    def _common_params(self, params: "lt.add_torrent_params", save_path: str, paused: bool) -> None:
        params.save_path = save_path
        params.max_connections = int(self.settings["max_peers_per_torrent"])
        params.max_uploads = int(self.settings["upload_slots_per_torrent"])
        params.storage_mode = (lt.storage_mode_t.storage_mode_allocate if self.settings["preallocate"]
                               else lt.storage_mode_t.storage_mode_sparse)
        flags = params.flags | lt.torrent_flags.auto_managed
        flags &= ~lt.torrent_flags.duplicate_is_error
        if paused:
            flags = (flags | lt.torrent_flags.paused) & ~lt.torrent_flags.auto_managed
        else:
            flags &= ~lt.torrent_flags.paused
        if not self.settings["pex"]:
            flags |= lt.torrent_flags.disable_pex
        params.flags = flags

    def add_torrent_file(self, ti: "lt.torrent_info", save_path: str,
                         file_priorities: list[int] | None = None, paused: bool = False) -> str:
        key = key_from_ih(ti.info_hashes())
        if key in self.handles:
            raise TorrentError(f"«{ti.name()}» ya está en la lista.")
        needed = ti.total_size()
        if file_priorities:
            fs = ti.files()
            needed = sum(fs.file_size(i) for i, p in enumerate(file_priorities) if p > 0)
        # Solo exigimos espacio completo si se preasigna; en modo disperso avisamos en la UI.
        self._check_dest(save_path, needed if self.settings["preallocate"] else 0)
        params = lt.add_torrent_params()
        params.ti = ti
        self._common_params(params, save_path, paused)
        if file_priorities:
            params.file_priorities = file_priorities
        if self.settings["add_public_trackers"] and not ti.priv():
            existing = {t.url for t in ti.trackers()}
            params.trackers = [u for u in self.settings["public_trackers"] if u not in existing]
        self.meta[key] = TorrentMeta()
        self.session.async_add_torrent(params)
        return key

    def add_magnet(self, uri: str, save_path: str, await_selection: bool = True,
                   paused: bool = False) -> str:
        params = self.parse_magnet(uri)
        key = key_from_ih(params.info_hashes)
        if key in self.handles:
            raise TorrentError("Ese enlace magnet ya está en la lista.")
        self._check_dest(save_path)
        self._common_params(params, save_path, paused)
        meta = TorrentMeta(pending_selection=await_selection)
        if await_selection:
            # En modo «solo subida» se descargan los metadatos pero ninguna pieza,
            # así el usuario puede elegir archivos antes de que empiece la descarga.
            params.flags |= lt.torrent_flags.upload_mode
        self.meta[key] = meta
        self._save_pending()
        self.session.async_add_torrent(params)
        return key

    def confirm_selection(self, key: str, save_path: str, priorities: list[int], start: bool = True) -> None:
        h = self.handles.get(key)
        if not h or not h.is_valid():
            return
        meta = self.meta.setdefault(key, TorrentMeta())
        ti = h.torrent_file()
        if ti is not None:
            fs = ti.files()
            needed = sum(fs.file_size(i) for i, p in enumerate(priorities) if p > 0)
            self._check_dest(save_path, needed if self.settings["preallocate"] else 0)
        st = h.status()
        if os.path.normcase(os.path.abspath(st.save_path)) != os.path.normcase(os.path.abspath(save_path)):
            h.move_storage(save_path)
        h.prioritize_files(priorities)
        h.unset_flags(lt.torrent_flags.upload_mode)
        meta.pending_selection = False
        self._save_pending()
        self._maybe_add_public_trackers(h)
        if not start:
            self.pause(key)
        h.save_resume_data(lt.torrent_handle.save_info_dict)
        self._outstanding_resume += 1

    def _maybe_add_public_trackers(self, h: "lt.torrent_handle") -> None:
        if self.settings["add_public_trackers"]:
            self._add_trackers_to_handle(h)

    def _add_trackers_to_handle(self, h: "lt.torrent_handle") -> int:
        """Añade la lista de trackers públicos. Devuelve cuántos se añadieron."""
        ti = h.torrent_file()
        if ti is None or ti.priv():
            return 0  # nunca se añaden trackers a torrents privados
        current = h.trackers()
        existing = {t["url"] for t in current}
        tier = max([t["tier"] for t in current] + [0]) + 1
        added = 0
        for url in self.settings["public_trackers"]:
            if url not in existing:
                h.add_tracker({"url": url, "tier": tier})
                added += 1
        if added:
            h.force_reannounce()
            if self.settings["dht"]:
                h.force_dht_announce()
            h.save_resume_data(lt.torrent_handle.save_info_dict)
            self._outstanding_resume += 1
        return added

    def add_public_trackers(self, key: str) -> tuple[int, str]:
        """Añade los trackers públicos a un torrent ya existente (acción manual).
        Devuelve (añadidos, motivo si no se añadió ninguno)."""
        h = self._h(key)
        if not h:
            return 0, "El torrent ya no existe."
        ti = h.torrent_file()
        if ti is None:
            return 0, "Aún no se han recibido los metadatos; inténtalo cuando aparezca el tamaño."
        if ti.priv():
            return 0, "Es un torrent privado: su tracker no permite fuentes externas."
        if not self.settings["public_trackers"]:
            return 0, "La lista de trackers públicos está vacía (Ajustes → Trackers)."
        added = self._add_trackers_to_handle(h)
        return added, "" if added else "Ya tenía todos los trackers de la lista."

    # ------------------------------------------------------------------ acciones
    def _h(self, key: str) -> "lt.torrent_handle | None":
        h = self.handles.get(key)
        return h if h is not None and h.is_valid() else None

    def pause(self, key: str) -> None:
        h = self._h(key)
        if h:
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause(lt.torrent_handle.graceful_pause)

    def resume(self, key: str, force: bool = False) -> None:
        h = self._h(key)
        if h:
            h.clear_error()
            if force:
                h.unset_flags(lt.torrent_flags.auto_managed)
            else:
                h.set_flags(lt.torrent_flags.auto_managed)
            h.resume()

    def remove(self, key: str, delete_files: bool = False) -> None:
        h = self._h(key)
        if h:
            if delete_files:
                self.session.remove_torrent(h, lt.session.delete_files)
            else:
                self.session.remove_torrent(h)
        self.handles.pop(key, None)
        self.meta.pop(key, None)
        self.status.pop(key, None)
        for ext in (".fastresume",):
            try:
                (resume_dir() / f"{key}{ext}").unlink(missing_ok=True)
            except OSError:
                pass
        self._save_pending()

    def recheck(self, key: str) -> None:
        h = self._h(key)
        if h:
            h.clear_error()
            h.force_recheck()

    def queue(self, key: str, where: str) -> None:
        h = self._h(key)
        if not h:
            return
        {"arriba": h.queue_position_top, "subir": h.queue_position_up,
         "bajar": h.queue_position_down, "abajo": h.queue_position_bottom}[where]()

    def set_file_priority(self, key: str, indices: list[int], prio: int) -> None:
        h = self._h(key)
        if not h:
            return
        prios = list(h.get_file_priorities())
        for i in indices:
            if 0 <= i < len(prios):
                prios[i] = prio
        h.prioritize_files(prios)

    def open_folder_path(self, key: str) -> str:
        h = self._h(key)
        if not h:
            return ""
        st = self.status.get(key) or h.status()
        path = Path(st.save_path) / st.name
        return str(path if path.exists() else Path(st.save_path))

    # ------------------------------------------------------------------ consulta
    def files(self, key: str) -> list[dict]:
        h = self._h(key)
        if not h:
            return []
        ti = h.torrent_file()
        if ti is None:
            return []
        fs = ti.files()
        progress = h.file_progress(lt.torrent_handle.piece_granularity)
        prios = h.get_file_priorities()
        out = []
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            size = fs.file_size(i)
            done = progress[i] if i < len(progress) else 0
            out.append({
                "index": i, "path": fs.file_path(i), "size": size,
                "progress": (done / size) if size else 1.0,
                "priority": prios[i] if i < len(prios) else 4,
            })
        return out

    def peers(self, key: str) -> list[dict]:
        h = self._h(key)
        if not h:
            return []
        out = []
        for p in h.get_peer_info():
            client = p.client
            if isinstance(client, bytes):
                client = client.decode("utf-8", "replace")
            flags = []
            if not (p.flags & lt.peer_info.local_connection):
                flags.append("E")      # entrante
            if p.flags & (lt.peer_info.rc4_encrypted | lt.peer_info.plaintext_encrypted):
                flags.append("C")      # cifrado
            if p.source & lt.peer_info.dht:
                flags.append("D")
            if p.source & lt.peer_info.pex:
                flags.append("X")
            if p.source & lt.peer_info.lsd:
                flags.append("L")
            if p.flags & lt.peer_info.snubbed:
                flags.append("S")
            if p.flags & lt.peer_info.seed:
                flags.append("Sm")
            try:
                ip = f"{p.ip[0]}:{p.ip[1]}"
            except Exception:
                ip = str(p.ip)
            out.append({
                "ip": ip, "client": client or "?", "flags": " ".join(flags),
                "progress": p.progress, "down": p.payload_down_speed, "up": p.payload_up_speed,
                "downloaded": p.total_download, "uploaded": p.total_upload,
                "disk_wait": bool((p.read_state | p.write_state) & lt.peer_info.bw_disk),
            })
        return out

    def trackers(self, key: str) -> list[dict]:
        h = self._h(key)
        if not h:
            return []
        st = self.status.get(key)
        out = []
        for t in h.trackers():
            msg, fails, updating, seeds, peers, verified = "", 0, False, -1, -1, bool(t.get("verified"))
            err = ""
            for ep in t.get("endpoints") or []:
                for ihd in ep.get("info_hashes") or []:
                    if not isinstance(ihd, dict):
                        continue
                    msg = msg or ihd.get("message", "")
                    fails = max(fails, ihd.get("fails", 0) or 0)
                    updating = updating or bool(ihd.get("updating"))
                    seeds = max(seeds, ihd.get("scrape_complete", -1) if ihd.get("scrape_complete") is not None else -1)
                    peers = max(peers, ihd.get("scrape_incomplete", -1) if ihd.get("scrape_incomplete") is not None else -1)
                    le = ihd.get("last_error") or {}
                    if isinstance(le, dict) and le.get("value"):
                        err = err or f"error {le.get('value')}"
            msg = msg or t.get("message", "")
            if updating:
                state = "Anunciando…"
            elif verified:
                state = "Funciona"
            elif fails:
                state = f"Error ({fails} fallos)"
            else:
                state = "Sin contactar"
            out.append({"url": t["url"], "tier": t.get("tier", 0), "state": state,
                        "message": msg or err, "seeds": seeds, "peers": peers})
        extra = []
        if st is not None:
            ti = h.torrent_file()
            priv = bool(ti is not None and ti.priv())
            extra = [
                ("** [DHT] **", "Activo" if self.settings["dht"] and not priv else "Desactivado"),
                ("** [PeX] **", "Activo" if self.settings["pex"] and not priv else "Desactivado"),
                ("** [LSD] **", "Activo" if self.settings["lsd"] and not priv else "Desactivado"),
            ]
        return [{"url": u, "tier": "", "state": s, "message": "", "seeds": -1, "peers": -1}
                for u, s in extra] + out

    def state_text(self, key: str) -> str:
        st = self.status.get(key)
        if st is None:
            return "Añadiendo…"
        meta = self.meta.get(key)
        if st.errc and st.errc.value():
            return "Error"
        if meta and meta.pending_selection:
            return "Obteniendo metadatos" if not st.has_metadata else "Esperando selección"
        paused = bool(st.flags & lt.torrent_flags.paused)
        auto = bool(st.flags & lt.torrent_flags.auto_managed)
        if st.moving_storage:
            return "Moviendo archivos"
        if paused:
            if st.state in (lt.torrent_status.states.checking_files,
                            lt.torrent_status.states.checking_resume_data):
                return "En cola para comprobar"
            if auto:
                return "En cola (sembrar)" if st.is_seeding or st.is_finished else "En cola"
            return "Completado" if st.is_seeding or st.is_finished else "Pausado"
        if st.state == lt.torrent_status.states.downloading and st.download_payload_rate < 1024 and st.num_peers == 0:
            return "Buscando pares"
        if not auto and st.state == lt.torrent_status.states.downloading:
            return "Descargando [F]"
        return STATE_NAMES.get(st.state, str(st.state))

    def error_text(self, key: str) -> str:
        st = self.status.get(key)
        meta = self.meta.get(key)
        if meta and meta.last_error:
            return meta.last_error
        if st is not None and st.errc and st.errc.value():
            return explain_error(st.errc)
        return ""

    def port_status(self) -> tuple[str, str]:
        """Devuelve (estado, explicación). Estado: abierto | probable | cerrado | comprobando."""
        port = self.listen_port
        if self.bind_error:
            return "cerrado", f"Sin conexión: {self.bind_error}. Cámbiala en Ajustes → Conexión → Interfaz de red."
        if self.listen_error:
            return "cerrado", f"No se pudo escuchar en el puerto {port}: {self.listen_error}"
        if self.incoming_seen:
            return "abierto", f"Puerto {port} abierto: se han recibido conexiones entrantes."
        if self.portmap_ok:
            return "probable", f"Puerto {port} redirigido por UPnP/NAT-PMP; aún sin conexiones entrantes confirmadas."
        any_active = any(not (s.flags & lt.torrent_flags.paused) for s in self.status.values())
        if any_active and time.time() - self.started_at > 300:
            extra = f" Error de UPnP/NAT-PMP: {self.portmap_error}." if self.portmap_error else ""
            return "cerrado", (f"Puerto {port} probablemente cerrado: tras 5 minutos no ha entrado ninguna conexión."
                               f"{extra} Abre el puerto en el router o activa UPnP.")
        return "comprobando", f"Puerto {port}: comprobando (hacen falta torrents activos y unos minutos)."

    # ------------------------------------------------------------------ bucle
    def refresh(self) -> None:
        """Actualiza el estado de todos los torrents (llamar 1 vez por segundo)."""
        if not self.session:
            return
        total_down = total_up = 0
        for key, h in list(self.handles.items()):
            if not h.is_valid():
                continue
            try:
                st = h.status()
            except Exception:
                continue
            meta = self.meta.setdefault(key, TorrentMeta())
            prev = meta.prev_state
            if (prev == lt.torrent_status.states.downloading
                    and st.state in (lt.torrent_status.states.finished, lt.torrent_status.states.seeding)):
                self._events.append(("finished", key, st.name))
            meta.prev_state = st.state
            meta.history.append((st.download_payload_rate, st.upload_payload_rate))
            if st.has_incoming:
                self.incoming_seen = True
            self.status[key] = st
            total_down += st.download_payload_rate
            total_up += st.upload_payload_rate
        self.global_history.append((total_down, total_up))

        now = time.time()
        if self.settings["network_interface"] and now - self._last_bind_check > 15:
            # Si cambia la IP del adaptador (DHCP, reconexión) se vuelve a enlazar.
            self._last_bind_check = now
            self._apply_bind()
        if now - self._last_stats_post > 5:
            self._last_stats_post = now
            self.session.post_session_stats()
        if now - self._last_resume_save > 300:
            self._last_resume_save = now
            self.save_all_resume(only_modified=True)

    def poll(self) -> list[tuple]:
        """Procesa las alertas pendientes y devuelve eventos para la interfaz."""
        if not self.session:
            return []
        for a in self.session.pop_alerts():
            try:
                self._handle_alert(a)
            except Exception as exc:  # una alerta rara nunca debe tumbar la app
                self._events.append(("log", f"Error procesando alerta {type(a).__name__}: {exc}"))
        events, self._events = self._events, []
        return events

    def _handle_alert(self, a: Any) -> None:
        if isinstance(a, lt.add_torrent_alert):
            if a.error.value():
                name = a.params.name or (a.params.ti.name() if a.params.ti else "torrent")
                self._events.append(("error", None, f"No se pudo añadir «{name}»: {explain_error(a.error)}"))
                return
            h = a.handle
            key = key_of(h)
            self.handles[key] = h
            meta = self.meta.setdefault(key, TorrentMeta())
            if meta.pending_selection and h.status().has_metadata:
                self._events.append(("needs_selection", key))
            elif not meta.pending_selection and h.status().has_metadata:
                # torrents añadidos por archivo ya tienen los trackers en params
                pass
            self._events.append(("added", key))
            h.save_resume_data(lt.torrent_handle.save_info_dict)
            self._outstanding_resume += 1

        elif isinstance(a, lt.metadata_received_alert):
            key = key_of(a.handle)
            meta = self.meta.setdefault(key, TorrentMeta())
            if meta.pending_selection:
                self._events.append(("needs_selection", key))
            else:
                self._maybe_add_public_trackers(a.handle)
            a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            self._outstanding_resume += 1
            self._events.append(("metadata", key))

        elif isinstance(a, lt.save_resume_data_alert):
            self._outstanding_resume = max(0, self._outstanding_resume - 1)
            try:
                key = key_of(a.handle)
                if key not in self.handles:
                    return
                data = lt.write_resume_data_buf(a.params)
                path = resume_dir() / f"{key}.fastresume"
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except Exception as exc:
                self._events.append(("log", f"No se pudo guardar el estado de reanudación: {exc}"))

        elif isinstance(a, lt.save_resume_data_failed_alert):
            self._outstanding_resume = max(0, self._outstanding_resume - 1)

        elif isinstance(a, (lt.file_error_alert, lt.torrent_error_alert)):
            key = key_of(a.handle) if a.handle.is_valid() else None
            fname = ""
            try:
                fname = a.filename()
            except Exception:
                pass
            text = explain_error(a.error, fname)
            if key in self.meta:
                self.meta[key].last_error = text
            name = a.handle.status().name if key else ""
            self._events.append(("error", key, f"«{name}»: {text}" if name else text))

        elif isinstance(a, lt.hash_failed_alert):
            key = key_of(a.handle)
            if key in self.meta:
                self.meta[key].hash_fails += 1

        elif isinstance(a, lt.performance_alert):
            code = int(a.warning_code)
            self.global_perf.append((time.time(), code))
            try:
                key = key_of(a.handle)
                if key in self.meta:
                    self.meta[key].perf_warnings.append((time.time(), code))
            except Exception:
                pass

        elif isinstance(a, lt.listen_succeeded_alert):
            self.listen_error = ""

        elif isinstance(a, lt.listen_failed_alert):
            # Fallar solo en IPv6 es habitual y no es un problema.
            addr = str(getattr(a, "address", ""))
            if ":" not in addr:
                self.listen_error = explain_error(a.error)
                self._events.append(("error", None, f"No se puede abrir el puerto {self.listen_port}: {self.listen_error}"))

        elif isinstance(a, lt.portmap_alert):
            self.portmap_ok = True
            self.portmap_error = ""

        elif isinstance(a, lt.portmap_error_alert):
            if not self.portmap_ok:
                self.portmap_error = a.message()

        elif isinstance(a, lt.session_stats_alert):
            values = a.values
            if isinstance(values, dict):
                self.dht_nodes = int(values.get("dht.dht_nodes", 0))

        elif isinstance(a, lt.torrent_removed_alert):
            self._events.append(("removed", None))

        elif isinstance(a, lt.torrent_deleted_alert):
            pass

        elif isinstance(a, lt.torrent_delete_failed_alert):
            self._events.append(("error", None, f"No se pudieron borrar los archivos: {explain_error(a.error)}"))

        elif isinstance(a, lt.storage_moved_failed_alert):
            self._events.append(("error", None, f"No se pudo mover la descarga: {explain_error(a.error)}"))

        elif isinstance(a, lt.fastresume_rejected_alert):
            self._events.append(("log", f"Datos de reanudación rechazados, se recomprobará: {a.message()}"))

    # ------------------------------------------------------------------ reanudación
    def _save_pending(self) -> None:
        pending = [k for k, m in self.meta.items() if m.pending_selection]
        try:
            self._pending_file.write_text(json.dumps(pending), encoding="utf-8")
        except OSError:
            pass

    def _load_resume_data(self) -> None:
        pending: set[str] = set()
        try:
            pending = set(json.loads(self._pending_file.read_text(encoding="utf-8")))
        except Exception:
            pass
        for path in sorted(resume_dir().glob("*.fastresume")):
            try:
                params = lt.read_resume_data(path.read_bytes())
            except Exception as exc:
                self._events.append(("log", f"Archivo de reanudación dañado ignorado ({path.name}): {exc}"))
                continue
            key = path.stem
            self.meta[key] = TorrentMeta(pending_selection=key in pending)
            self.session.async_add_torrent(params)

    def save_all_resume(self, only_modified: bool = False) -> int:
        count = 0
        flags = lt.torrent_handle.save_info_dict
        if only_modified:
            flags |= lt.torrent_handle.only_if_modified
        for h in self.handles.values():
            if h.is_valid() and (not only_modified or h.need_save_resume_data()):
                h.save_resume_data(flags)
                count += 1
        self._outstanding_resume += count
        return count

    def shutdown(self, timeout: float = 10.0) -> None:
        if not self.session:
            return
        self.session.pause()
        self._outstanding_resume = 0
        self.save_all_resume(only_modified=False)
        deadline = time.time() + timeout
        while self._outstanding_resume > 0 and time.time() < deadline:
            self.session.wait_for_alert(500)
            self.poll()
        self._save_pending()
        self.session = None

    # ------------------------------------------------------------------ diagnóstico
    def diagnose(self, key: str, interfaces: list[str]) -> list[dict]:
        """Analiza un torrent y devuelve causas probables de lentitud,
        ordenadas de más a menos importante.
        Cada elemento: {nivel: alto|medio|info, titulo, detalle}."""
        h = self._h(key)
        st = self.status.get(key)
        meta = self.meta.get(key, TorrentMeta())
        out: list[dict] = []
        if not h or st is None:
            return [{"nivel": "info", "titulo": "Selecciona un torrent", "detalle": "No hay datos de este torrent todavía."}]

        def add(nivel, titulo, detalle):
            out.append({"nivel": nivel, "titulo": titulo, "detalle": detalle})

        now = time.time()
        states = lt.torrent_status.states
        paused = bool(st.flags & lt.torrent_flags.paused)
        auto = bool(st.flags & lt.torrent_flags.auto_managed)

        # 1. Estados que explican por sí mismos que no haya descarga
        if st.errc and st.errc.value() or meta.last_error:
            add("alto", "El torrent tiene un error",
                (meta.last_error or explain_error(st.errc)) + " Corrige el problema y pulsa «Reanudar» o «Forzar recomprobación».")
        if st.is_seeding or st.is_finished:
            add("info", "La descarga ya está completa",
                "Este torrent ya tiene todo lo que pediste; ahora solo sube datos a otros.")
        if meta.pending_selection:
            add("medio", "Esperando a que elijas los archivos",
                "Los metadatos están listos: selecciona qué archivos descargar para empezar.")
        if paused and auto:
            add("alto", "Está en cola",
                f"Solo se permiten {self.settings['active_downloads']} descargas activas a la vez. "
                "Pausa otros torrents, súbelo en la cola o aumenta el límite en Ajustes → Cola.")
        elif paused:
            add("alto", "Está pausado", "Pulsa «Reanudar» para que empiece a descargar.")
        if st.state in (states.checking_files, states.checking_resume_data):
            add("medio", "Comprobando archivos",
                "Se está verificando lo ya descargado. Mientras dura no se descarga nada.")
        if st.state == states.downloading_metadata:
            add("medio", "Esperando metadatos",
                "Es un enlace magnet y todavía no se ha encontrado a nadie que envíe la información del torrent. "
                "Si tarda mucho suele significar que no queda nadie compartiéndolo.")

        # 2. Disponibilidad: semillas y copias
        seeds_total = st.num_complete if st.num_complete >= 0 else st.list_seeds
        peers_total = st.num_incomplete if st.num_incomplete >= 0 else st.list_peers
        if not (st.is_seeding or st.is_finished) and st.has_metadata:
            if seeds_total == 0 and st.distributed_copies < 1.0 and st.num_peers > 0:
                add("alto", "No hay ninguna copia completa disponible",
                    f"Ninguna semilla conectada y entre todos los pares solo existe el "
                    f"{max(st.distributed_copies, 0) * 100:.0f} % del archivo. Puede que nunca llegue al 100 % "
                    "hasta que vuelva a conectarse alguien con la copia completa.")
            elif seeds_total <= 2 and st.num_seeds <= 1:
                add("alto", "Muy pocas semillas",
                    f"Solo se conocen {max(seeds_total, 0)} semillas ({st.num_seeds} conectadas). "
                    "La velocidad depende de lo que puedan subir esas pocas personas. "
                    "Prueba en otro momento del día o busca otra versión con más semillas.")
            elif st.num_peers + st.num_seeds < 5 and (seeds_total + max(peers_total, 0)) < 10:
                add("medio", "Torrent poco compartido",
                    f"Hay muy pocas personas en este enjambre ({max(seeds_total, 0)} semillas, "
                    f"{max(peers_total, 0)} pares). Activar los trackers públicos adicionales puede ayudar.")

        # 3. Red: puerto, VPN, DHT, trackers
        port_state, port_text = self.port_status()
        bound = self.settings["network_interface"]
        if bound:
            # Si Corriente está atado a un adaptador concreto, solo importa si ese es una VPN.
            vpns = [bound] if is_vpn_interface(bound) else []
        else:
            vpns = [n for n in interfaces if is_vpn_interface(n)]
        if port_state == "cerrado":
            add("alto", "El puerto parece cerrado",
                port_text + " Con el puerto cerrado solo puedes conectar con pares que lo tengan abierto, "
                "lo que reduce mucho las fuentes disponibles.")
        if vpns and not self.incoming_seen:
            add("alto" if port_state == "cerrado" else "medio", "Una VPN puede estar bloqueando conexiones entrantes",
                f"Se ha detectado una conexión VPN ({', '.join(vpns[:3])}). La mayoría de VPN no dejan entrar "
                "conexiones salvo que actives el «reenvío de puertos» (port forwarding) en la VPN y pongas ese "
                "mismo puerto en Ajustes → Conexión. Otra opción: excluye Corriente en el «split tunneling» de la "
                "VPN y elige tu adaptador físico (Ethernet o Wi-Fi) en Ajustes → Conexión → Interfaz de red.")
        if self.settings["dht"] and self.dht_nodes < 10 and now - self.started_at > 120:
            add("medio", "La red DHT apenas tiene nodos",
                f"Solo hay {self.dht_nodes} nodos DHT. El firewall o la VPN podrían estar bloqueando el tráfico UDP. "
                "Permite «Corriente» en el Firewall de Windows (redes privadas y públicas).")
        if not self.settings["dht"]:
            add("info", "DHT desactivado", "Activar DHT en Ajustes → Conexión ayuda a encontrar más pares.")
        trackers = self.trackers(key)
        real = [t for t in trackers if not t["url"].startswith("**")]
        if real and all(t["state"].startswith("Error") for t in real):
            add("medio", "Ningún tracker responde",
                "Todos los trackers fallan, así que los pares solo llegan por DHT/PeX. "
                "Haz clic derecho sobre el torrent → «Añadir trackers públicos» (si el torrent no es privado).")
        elif not real and not self.settings["dht"]:
            add("alto", "Sin trackers ni DHT", "Este torrent no tiene trackers y el DHT está desactivado: no hay forma de encontrar pares.")

        # 4. Límites de velocidad configurados
        up_limit = self.settings.effective_upload_limit_bytes()
        if 0 < up_limit < 20 * 1024:
            add("alto", "Límite de subida demasiado bajo",
                f"La subida está limitada a {up_limit // 1024} KiB/s. BitTorrent premia a quien comparte: "
                "con tan poca subida los demás te dan menos prioridad, y además las confirmaciones TCP de tu "
                "descarga se quedan atascadas. Sube el límite a 30 KiB/s o más.")
        recent = [c for t, c in list(meta.perf_warnings) + list(self.global_perf) if now - t < 60]
        if int(lt.performance_warning_t.upload_limit_too_low) in recent:
            add("alto", "libtorrent avisa: subida insuficiente",
                "El propio motor ha detectado que el límite de subida está frenando la descarga.")
        dl_limit = self.settings.download_limit_bytes()
        if dl_limit and st.download_payload_rate >= dl_limit * 0.9:
            add("medio", "Has alcanzado tu límite de bajada",
                f"La bajada está limitada a {dl_limit // 1024} KiB/s en Ajustes → Velocidad.")

        # 5. Disco
        disk_codes = {int(lt.performance_warning_t.outstanding_disk_buffer_limit_reached),
                      int(lt.performance_warning_t.too_high_disk_queue_limit)}
        disk_waiting = 0
        if not paused:
            try:
                disk_waiting = sum(1 for p in self.peers(key) if p["disk_wait"])
            except Exception:
                pass
        if disk_codes & set(recent) or disk_waiting >= 3:
            add("alto", "El disco está saturado",
                f"El disco no da abasto para escribir lo que llega ({disk_waiting} conexiones esperando al disco). "
                "Ocurre con discos duros lentos, USB o cuando otro programa usa mucho el disco. "
                "Descarga a un SSD, reduce las descargas simultáneas o desactiva la preasignación.")
        try:
            free = shutil.disk_usage(st.save_path).free
            remaining = st.total_wanted - st.total_wanted_done
            if not self.settings["preallocate"] and free < remaining:
                add("alto", "No cabrá en el disco",
                    f"Faltan {remaining / 1024**3:.2f} GB y solo quedan {free / 1024**3:.2f} GB libres.")
        except OSError:
            pass

        # 6. Datos corruptos
        if meta.hash_fails >= 3 or (st.total_download and st.total_failed_bytes > 0.05 * st.total_download):
            add("medio", "Errores de verificación (hash)",
                f"{meta.hash_fails} piezas llegaron dañadas y hubo que descargarlas de nuevo "
                f"({st.total_failed_bytes / 1024**2:.1f} MB desperdiciados). Suele deberse a pares defectuosos "
                "o maliciosos; libtorrent los bloquea automáticamente. Si persiste, el problema puede ser la RAM o el disco.")

        # 7. Conexiones
        if (not paused and st.state == states.downloading
                and st.num_connections >= int(self.settings["max_peers_per_torrent"]) * 0.95):
            add("info", "Límite de conexiones alcanzado",
                "El torrent usa todas las conexiones permitidas; normalmente no es un problema.")

        if not out:
            add("info", "No se detecta ningún problema evidente",
                f"Conectado a {st.num_seeds} semillas y {st.num_peers - st.num_seeds} pares. La velocidad depende "
                "de lo que puedan subir ellos; los torrents tardan un par de minutos en alcanzar su máxima velocidad.")
        order = {"alto": 0, "medio": 1, "info": 2}
        out.sort(key=lambda d: order[d["nivel"]])
        return out
