"""Punto de entrada de Corriente.

- Garantiza una sola instancia: si ya hay una abierta, le pasa los argumentos
  (.torrent / magnet:) por un QLocalServer y sale.
- Arranca la sesión de libtorrent y la ventana principal.
"""
from __future__ import annotations

import getpass
import logging
import sys
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from settings import APP_ID, APP_NAME, Settings, data_dir

SERVER_NAME = f"{APP_NAME}-{getpass.getuser()}"

logging.basicConfig(filename=str(data_dir() / "corriente.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")


def send_to_running_instance(args: list[str]) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(500):
        return False
    sock.write(("\n".join(args) if args else "--show").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.error("Excepción no controlada:\n%s", text)
        try:
            QMessageBox.critical(None, APP_NAME, f"Se ha producido un error inesperado:\n\n{exc}\n\n"
                                                 f"Detalles en {data_dir() / 'corriente.log'}")
        except Exception:
            pass
    sys.excepthook = hook


def main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if send_to_running_instance(args):
        return 0

    install_excepthook()

    try:
        import libtorrent  # noqa: F401
    except ImportError as exc:
        QMessageBox.critical(None, APP_NAME,
                             "No se pudo cargar libtorrent.\n\n"
                             "Instálalo con:  pip install libtorrent\n"
                             "Si ya está instalado y falla al cargar una DLL, instala el "
                             "«Microsoft Visual C++ Redistributable» (x64) más reciente.\n\n"
                             f"Detalle: {exc}")
        return 1

    from engine import TorrentEngine
    from ui import MainWindow, apply_theme, first_run

    settings = Settings()
    apply_theme(app, settings["theme"])

    engine = TorrentEngine(settings)
    try:
        engine.start()
    except Exception as exc:
        logging.exception("Fallo al iniciar la sesión")
        QMessageBox.critical(None, APP_NAME, f"No se pudo iniciar el motor BitTorrent:\n{exc}")
        return 1

    window = MainWindow(engine, settings)

    # Servidor de instancia única
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    def on_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return

        def read():
            data = bytes(sock.readAll()).decode("utf-8", "replace")
            items = [l for l in data.splitlines() if l and l != "--show"]
            window.show_window()
            if items:
                window.handle_args(items)
            sock.deleteLater()

        if sock.bytesAvailable():
            read()
        else:
            sock.readyRead.connect(read)

    server.newConnection.connect(on_connection)

    if settings["start_minimized"] and not args:
        window.hide()
    else:
        window.show()

    def after_start():
        first_run(window, settings)
        if args:
            window.handle_args(args)

    QTimer.singleShot(200, after_start)
    app.aboutToQuit.connect(lambda: engine.session and engine.shutdown())
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
