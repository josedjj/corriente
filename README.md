# Corriente — BitTorrent client for Windows

[Español](README.es.md) · **English**

A free, open-source desktop BitTorrent client for Windows 10/11, built on **libtorrent 2.x** with a **PySide6** interface.

- **No ads**, no telemetry, no accounts. It never sends data to any server of its own.
- Spanish-language interface with dark and light themes.
- MIT licensed: use it, modify it and share it freely.

## Download

Go to [**Releases**](../../releases/latest), download `Corriente-windows-x64.zip`, unzip it anywhere and run
`Corriente.exe`. No installation and no Python required.

> The executable is not digitally signed, so Windows SmartScreen may show "Windows protected your PC".
> Click **More info → Run anyway**. You can also build it yourself from source (see below).

## Project layout

| File | Purpose |
|---|---|
| `main.py` | Entry point, single instance (forwards `.torrent`/magnet links to the open window), error logging |
| `engine.py` | libtorrent session: add/pause/remove, alerts, resume data, port status, "Why is it slow?" diagnostics |
| `ui.py` | Main window, dialogs (add, magnet, settings, diagnostics), system tray, speed graph, themes |
| `settings.py` | Persistent settings in `%APPDATA%\Corriente\settings.json` |
| `netif.py` | Network adapter list (to pick an interface, or route inside/outside a VPN) |
| `winassoc.py` | `.torrent` and `magnet:` association in the registry (HKCU, no admin rights needed) |
| `build.ps1` | PyInstaller packaging |

## Run from source

Requires 64-bit Python 3.11 or newer (tested with 3.13).

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

If `import libtorrent` fails with a DLL error, install the latest **Microsoft Visual C++ Redistributable (x64)**.

## Build the .exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1           # dist\Corriente\ folder (recommended: starts faster)
powershell -ExecutionPolicy Bypass -File .\build.ps1 -OneFile  # single dist\Corriente.exe
```

The script creates the virtual environment, installs `requirements-build.txt`, generates the icon and runs:

```
pyinstaller main.py --name Corriente --windowed --onedir --icon assets\corriente.ico --collect-all libtorrent
```

To distribute it, zip the whole `dist\Corriente` folder. For double-clicking `.torrent` files to open the .exe,
enable the association **from the .exe** (Ajustes → General), not from the Python version.

## Features

- Open `.torrent` files with the button, by drag and drop, or by double-clicking them in File Explorer.
- Paste magnet links (Ctrl+M; a magnet link already on the clipboard is filled in automatically).
- When adding: choose the destination folder, the files and a per-file priority, with a free-space warning.
  For magnet links the dialog appears once the metadata arrives; no pieces are downloaded until then.
- Torrent list with name, size, progress, download/upload speed, seeds and peers (connected and total),
  ETA, status and ratio.
- Pause, resume, force start, remove (optionally deleting the files), force recheck,
  queue priority (up, down, top, bottom) and per-file priority.
- Details panel: General, Files, Peers, Trackers, Speed (5-minute graph) and Log.
- Resumes after closing (`%APPDATA%\Corriente\resume\*.fastresume`, saved periodically and on exit).
- System tray: closing or minimizing keeps it running in the background, with a notification when a download finishes.
- Dark and light themes (View menu or Settings).

## Default settings

| Setting | Value |
|---|---|
| Port | random between 49152 and 65535 (chosen once and kept), UPnP and NAT-PMP enabled |
| Peer discovery | DHT, PeX and LSD enabled |
| Encryption | enabled (preferred, not forced) |
| Connections | 800 total, 150 per torrent, 4 upload slots per torrent |
| Speed | upload capped at 80 % of the speed you enter (asked on first run), unlimited download |
| Queue | 5 active torrents, 3 active downloads (inactive torrents don't count) |
| Disk | 8 I/O threads, 2 hashing threads, 4 MiB send buffer, OS cache enabled, sparse files (full preallocation optional) |
| Public trackers | editable list, off by default for new torrents; it can also be added to an existing torrent via right-click → "Añadir trackers públicos". Never added to private torrents |

## VPN and split tunneling

In Ajustes → Conexión → **Interfaz de red** (Settings → Connection → Network interface) you can bind Corriente
to a specific adapter. To use it outside the VPN, exclude `Corriente.exe` in the VPN's split tunneling, pick your
physical adapter (Ethernet or Wi-Fi) and restart Corriente. If you pick the VPN adapter, Corriente only works
while the VPN is connected. If the chosen adapter disconnects, Corriente stays offline instead of switching to another one.

Some VPNs (for example NordVPN) block traffic from apps excluded via split tunneling on both the VPN and the
physical adapter. If nothing connects, remove Corriente from the split tunneling list and bind it to the VPN
adapter, or disconnect the VPN.

## Diagnostics

- **Status bar**: port status (open / forwarded / closed / checking), DHT nodes and total speed.
  The port is reported open once an incoming connection arrives. If none arrives within 5 minutes with active
  torrents, it is marked as closed.
- **¿Por qué va lento?** ("Why is it slow?", F1): analyzes the selected torrent and explains the likely causes in
  plain language, ordered by importance: queued or paused, few seeds or no complete copy available, closed port,
  VPN detected with no incoming connections, DHT with no nodes, trackers down, upload limit too low, download limit
  reached, saturated disk (libtorrent performance warnings and peers waiting on disk), low disk space and hash errors.

## Errors

libtorrent errors are translated into clear messages: disk full, permission denied, file in use by another
program, path too long, drive disconnected, invalid torrent or magnet link, duplicate torrent and port in use.
Unexpected errors are logged to `%APPDATA%\Corriente\corriente.log`.

## Note on Windows 10/11

If you already chose another program with "Always open with", Windows keeps that choice over Corriente's
association. In that case, pick Corriente once in *Settings → Apps → Default apps*; Ajustes → General has a
button that opens that screen.

## Responsible use

Corriente is a general-purpose tool, like any other BitTorrent client: use it for Linux distributions, free
software, public-domain or Creative Commons works, your own files and any content you have the right to share.
Each user is responsible for what they download and share, and for complying with the laws of their country.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

- Release binaries are built from this repository by GitHub Actions ([build workflow](.github/workflows/build.yml));
  nothing built on a personal machine is signed.
- Committers and reviewers: [members of this repository](../../graphs/contributors)
- Approvers: [josedjj](https://github.com/josedjj)

### Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the
user or the person operating it. Connecting to BitTorrent peers, trackers and the DHT only happens for the
torrents the user adds. Corriente has no telemetry, no analytics, no ads and no update checks.

## Contributing

Bug reports and improvements are welcome: open an *issue* or a *pull request*.
