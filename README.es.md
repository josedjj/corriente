# Corriente — cliente BitTorrent para Windows

**Español** · [English](README.md)

Cliente BitTorrent de escritorio para Windows 10/11, gratuito y de código abierto, con motor **libtorrent 2.x**
e interfaz **PySide6**.

- **Sin publicidad**, sin telemetría y sin cuentas. No envía datos a ningún servidor propio.
- Interfaz en español, con tema oscuro y claro.
- Licencia MIT: puedes usarlo, modificarlo y compartirlo libremente.

## Descargar

Ve a [**Releases**](../../releases/latest), descarga `Corriente-windows-x64.zip`, descomprímelo donde quieras
y abre `Corriente.exe`. No necesita instalación ni Python.

> Como el ejecutable no está firmado digitalmente, Windows SmartScreen puede mostrar «Windows protegió su PC».
> Pulsa **Más información → Ejecutar de todas formas**. Si prefieres, puedes compilarlo tú mismo desde el código (ver abajo).

## Estructura

| Archivo | Qué hace |
|---|---|
| `main.py` | Punto de entrada, instancia única (reenvía `.torrent`/magnet a la ventana abierta), registro de errores |
| `engine.py` | Sesión de libtorrent: añadir/pausar/eliminar, alertas, datos de reanudación, estado del puerto, diagnóstico «¿Por qué va lento?» |
| `ui.py` | Ventana principal, diálogos (añadir, magnet, ajustes, diagnóstico), bandeja, gráfico de velocidad, temas |
| `settings.py` | Configuración persistente en `%APPDATA%\Corriente\settings.json` |
| `netif.py` | Lista de adaptadores de red (para elegir interfaz o salir por/fuera de la VPN) |
| `winassoc.py` | Asociación de `.torrent` y `magnet:` en el registro (HKCU, sin permisos de administrador) |
| `build.ps1` | Empaquetado con PyInstaller |

## Ejecutar desde el código

Requiere Python 3.11 o superior (probado con 3.13), 64 bits.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

Si `import libtorrent` falla con un error de DLL, instala el **Microsoft Visual C++ Redistributable x64** más reciente.

## Crear el .exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1           # carpeta dist\Corriente\ (recomendado: arranca más rápido)
powershell -ExecutionPolicy Bypass -File .\build.ps1 -OneFile  # un único dist\Corriente.exe
```

El script crea el entorno virtual, instala `requirements-build.txt`, genera el icono y ejecuta:

```
pyinstaller main.py --name Corriente --windowed --onedir --icon assets\corriente.ico --collect-all libtorrent
```

Para distribuirlo, comprime la carpeta `dist\Corriente` completa. Para que el doble clic en `.torrent` apunte
al .exe, activa la asociación **desde el .exe** (Ajustes → General), no desde la versión en Python.

## Funciones

- Abrir `.torrent` con el botón, arrastrando y soltando o con doble clic desde el Explorador.
- Pegar enlaces magnet (Ctrl+M; si hay uno en el portapapeles, se rellena solo).
- Al añadir: elegir carpeta de destino, archivos y prioridad por archivo, con aviso de espacio libre.
  En los magnet, el diálogo aparece al recibir los metadatos; hasta entonces no se descarga ninguna pieza.
- Lista con nombre, tamaño, progreso, bajada/subida, semillas y pares (conectados y totales), ETA, estado y ratio.
- Pausar, reanudar, forzar inicio, eliminar (con opción de borrar archivos), forzar recomprobación,
  prioridad en la cola (subir, bajar, arriba, abajo) y prioridad por archivo.
- Panel de detalles: General, Archivos, Pares, Trackers, Velocidad (gráfico de 5 min) y Registro.
- Reanudación tras cerrar (`%APPDATA%\Corriente\resume\*.fastresume`, guardado periódico y al salir).
- Bandeja del sistema: cerrar o minimizar la deja en segundo plano y avisa cuando termina una descarga.
- Tema oscuro y claro (menú Ver o Ajustes).

## Ajustes por defecto

| Ajuste | Valor |
|---|---|
| Puerto | aleatorio entre 49152 y 65535 (se elige una vez y se mantiene), UPnP y NAT-PMP activados |
| Búsqueda de pares | DHT, PeX y LSD activados |
| Cifrado | habilitado (preferido, sin forzar) |
| Conexiones | 800 en total, 150 por torrent, 4 ranuras de subida por torrent |
| Velocidad | subida al 80 % de la que indiques (se pregunta en el primer arranque), bajada ilimitada |
| Cola | 5 torrents activos, 3 descargas activas (los torrents sin actividad no cuentan) |
| Disco | 8 hilos de E/S, 2 de verificación, búfer de envío de 4 MiB, caché del sistema activa, archivos dispersos (la preasignación completa es opcional) |
| Trackers públicos | lista editable, desactivada por defecto para torrents nuevos; también se puede añadir a uno ya agregado con clic derecho → «Añadir trackers públicos». Nunca se añade a torrents privados |

## VPN y split tunneling

En Ajustes → Conexión → **Interfaz de red** se puede atar Corriente a un adaptador concreto. Para usarlo
fuera de la VPN, excluye `Corriente.exe` en el *split tunneling* de la VPN, elige tu adaptador físico
(Ethernet o Wi-Fi) y reinicia Corriente. Si eliges la VPN, Corriente solo funcionará con la VPN conectada.
Si el adaptador elegido se desconecta, Corriente se queda sin conexión en lugar de usar otro.

Algunas VPN (por ejemplo NordVPN) bloquean, tanto por la VPN como por el adaptador físico, el tráfico de las
apps excluidas con split tunneling. Si no conecta nada, quita Corriente del split tunneling y elige el adaptador
de la VPN, o desconecta la VPN.

## Diagnóstico

- **Barra de estado**: estado del puerto (abierto / redirigido / cerrado / comprobando), nodos DHT y velocidad total.
  El puerto se da por abierto cuando llega alguna conexión entrante. Si en 5 minutos con torrents activos
  no entra ninguna, se marca como cerrado.
- **¿Por qué va lento?** (F1): analiza el torrent seleccionado y explica en lenguaje sencillo las causas probables,
  ordenadas por importancia: en cola o pausado, pocas semillas o ninguna copia completa, puerto cerrado, VPN
  detectada sin conexiones entrantes, DHT sin nodos, trackers caídos, límite de subida demasiado bajo, límite
  de bajada alcanzado, disco saturado (avisos de rendimiento de libtorrent y pares esperando al disco),
  falta de espacio y errores de hash.

## Errores

Los errores de libtorrent se traducen a mensajes claros: disco lleno, permiso denegado, archivo en uso por
otro programa, ruta demasiado larga, unidad desconectada, torrent o magnet inválido, torrent duplicado y puerto
ocupado. Los errores no previstos se guardan en `%APPDATA%\Corriente\corriente.log`.

## Nota sobre Windows 10/11

Si ya elegiste otro programa con «Abrir siempre con», Windows mantiene esa elección por encima de la asociación
de Corriente. En ese caso elige Corriente una vez en *Configuración → Aplicaciones → Aplicaciones predeterminadas*;
en Ajustes → General hay un botón que abre esa pantalla.

## Uso responsable

Corriente es una herramienta de propósito general, igual que cualquier otro cliente BitTorrent: sirve para
distribuciones Linux, software libre, obras de dominio público o con licencia Creative Commons, archivos
propios y cualquier contenido que tengas derecho a compartir. Cada usuario es responsable de lo que descarga y
comparte, y de cumplir las leyes de su país.

## Contribuir

Los reportes de errores y las mejoras son bienvenidos: abre un *issue* o un *pull request*.
