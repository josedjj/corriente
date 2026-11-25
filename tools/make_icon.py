"""Genera assets/corriente.ico a partir del icono dibujado en ui.py."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from ui import make_app_icon  # noqa: E402

out = ROOT / "assets" / "corriente.ico"
out.parent.mkdir(exist_ok=True)
ok = make_app_icon().pixmap(QSize(256, 256)).save(str(out), "ICO")
print(("Icono generado: " if ok else "No se pudo generar el icono: ") + str(out))
sys.exit(0 if ok else 1)
