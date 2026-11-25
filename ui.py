"""Interfaz gráfica de Corriente (PySide6)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import libtorrent as lt
from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QPointF, QRectF, QSize,
                            QSortFilterProxyModel, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QFont, QGuiApplication, QIcon,
                           QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap)
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSpinBox, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionProgressBar,
                               QSystemTrayIcon, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextBrowser, QToolBar, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
                               QVBoxLayout, QWidget)

import netif
import winassoc
from engine import TorrentEngine, TorrentError, explain_error
from settings import APP_NAME, APP_VERSION, PORT_MAX, PORT_MIN, Settings, random_port

# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------

def fmt_size(n: float) -> str:
    if n is None or n < 0:
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TiB"


SPEED_UNIT = "bytes"   # "bytes" (MiB/s) o "bits" (Mbps, como anuncian los proveedores de internet)


def set_speed_unit(unit: str) -> None:
    global SPEED_UNIT
    SPEED_UNIT = "bits" if unit == "bits" else "bytes"


def fmt_rate(n: float) -> str:
    """Velocidad siempre con texto (incluye 0)."""
    n = max(0.0, float(n or 0))
    if SPEED_UNIT == "bits":
        bits = n * 8
        for unit in ("bps", "Kbps", "Mbps", "Gbps"):
            if bits < 1000 or unit == "Gbps":
                return f"{bits:.0f} {unit}" if unit == "bps" else f"{bits:.1f} {unit}"
            bits /= 1000
    return fmt_size(n) + "/s"


def fmt_speed(n: float) -> str:
    """Velocidad para tablas: vacío si es 0."""
    if not n or n < 1:
        return ""
    return fmt_rate(n)


def fmt_eta(seconds: float) -> str:
    if seconds is None or seconds <= 0 or seconds == float("inf"):
        return "∞"
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} d {h} h" if d < 100 else "∞"
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min {s} s"
    return f"{s} s"


PRIORITIES = [(0, "No descargar"), (1, "Baja"), (4, "Normal"), (7, "Alta")]


def prio_name(p: int) -> str:
    if p == 0:
        return "No descargar"
    if p <= 3:
        return "Baja"
    if p <= 5:
        return "Normal"
    return "Alta"


# ---------------------------------------------------------------------------
# Tema e icono
# ---------------------------------------------------------------------------

def apply_theme(app: QApplication, theme: str) -> None:
    app.setStyle("Fusion")
    if theme == "oscuro":
        p = QPalette()
        base, alt, text = QColor(30, 32, 36), QColor(40, 43, 48), QColor(225, 228, 232)
        p.setColor(QPalette.ColorRole.Window, QColor(37, 39, 44))
        p.setColor(QPalette.ColorRole.WindowText, text)
        p.setColor(QPalette.ColorRole.Base, base)
        p.setColor(QPalette.ColorRole.AlternateBase, alt)
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 53, 58))
        p.setColor(QPalette.ColorRole.ToolTipText, text)
        p.setColor(QPalette.ColorRole.Text, text)
        p.setColor(QPalette.ColorRole.Button, QColor(45, 48, 54))
        p.setColor(QPalette.ColorRole.ButtonText, text)
        p.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
        p.setColor(QPalette.ColorRole.Link, QColor(90, 170, 255))
        p.setColor(QPalette.ColorRole.Highlight, QColor(42, 120, 200))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(130, 134, 140))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            p.setColor(QPalette.ColorGroup.Disabled, role, QColor(120, 123, 128))
        app.setPalette(p)
    else:
        app.setPalette(app.style().standardPalette())


def make_app_icon() -> QIcon:
    """Icono dibujado por código: gota con flecha de descarga."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size
        p.setBrush(QColor(28, 140, 220))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92))
        pen = QPen(QColor(255, 255, 255), max(1.5, s * 0.11), Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.5, s * 0.22), QPointF(s * 0.5, s * 0.68))
        path = QPainterPath()
        path.moveTo(s * 0.30, s * 0.50)
        path.lineTo(s * 0.5, s * 0.70)
        path.lineTo(s * 0.70, s * 0.50)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.28, s * 0.80), QPointF(s * 0.72, s * 0.80))
        p.end()
        icon.addPixmap(pm)
    return icon


def dot_pixmap(color: QColor, size: int = 12) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(color)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return pm


def std_icon(widget: QWidget, name: str) -> QIcon:
    return widget.style().standardIcon(getattr(QStyle.StandardPixmap, name))


# ---------------------------------------------------------------------------
# Modelo de la lista de torrents
# ---------------------------------------------------------------------------

COLUMNS = ["#", "Nombre", "Tamaño", "Progreso", "Estado", "Semillas", "Pares",
           "Bajada", "Subida", "ETA", "Ratio"]
C_QUEUE, C_NAME, C_SIZE, C_PROGRESS, C_STATE, C_SEEDS, C_PEERS, C_DOWN, C_UP, C_ETA, C_RATIO = range(len(COLUMNS))
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2


class TorrentModel(QAbstractTableModel):
    def __init__(self, engine: TorrentEngine):
        super().__init__()
        self.engine = engine
        self.keys: list[str] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.keys)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def sync(self) -> None:
        current = [k for k in self.engine.handles if k in self.engine.status]
        cur_set = set(current)
        for row in reversed(range(len(self.keys))):
            if self.keys[row] not in cur_set:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self.keys[row]
                self.endRemoveRows()
        known = set(self.keys)
        new = [k for k in current if k not in known]
        if new:
            self.beginInsertRows(QModelIndex(), len(self.keys), len(self.keys) + len(new) - 1)
            self.keys.extend(new)
            self.endInsertRows()
        if self.keys:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.keys) - 1, len(COLUMNS) - 1))

    def _values(self, key: str, col: int):
        st = self.engine.status.get(key)
        if st is None:
            return "", 0
        if col == C_QUEUE:
            q = st.queue_position
            q = int(q) if not isinstance(q, int) else q
            return ("" if q < 0 else str(q + 1)), (q if q >= 0 else 1 << 30)
        if col == C_NAME:
            return st.name or key, (st.name or key).lower()
        if col == C_SIZE:
            size = st.total_wanted if st.has_metadata else -1
            return fmt_size(size), size
        if col == C_PROGRESS:
            return f"{st.progress * 100:.1f} %", st.progress
        if col == C_STATE:
            text = self.engine.state_text(key)
            return text, text
        if col == C_SEEDS:
            total = st.num_complete if st.num_complete >= 0 else st.list_seeds
            return f"{st.num_seeds} ({total})", st.num_seeds
        if col == C_PEERS:
            conn = st.num_peers - st.num_seeds
            total = st.num_incomplete if st.num_incomplete >= 0 else st.list_peers
            return f"{conn} ({total})", conn
        if col == C_DOWN:
            return fmt_speed(st.download_payload_rate), st.download_payload_rate
        if col == C_UP:
            return fmt_speed(st.upload_payload_rate), st.upload_payload_rate
        if col == C_ETA:
            if st.is_seeding or st.is_finished:
                return "", -1
            rate = st.download_payload_rate
            eta = (st.total_wanted - st.total_wanted_done) / rate if rate > 0 else float("inf")
            return fmt_eta(eta), eta
        if col == C_RATIO:
            down = st.all_time_download
            ratio = st.all_time_upload / down if down else 0.0
            return f"{ratio:.2f}", ratio
        return "", 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.keys):
            return None
        key = self.keys[index.row()]
        col = index.column()
        if role == KEY_ROLE:
            return key
        if role == Qt.ItemDataRole.DisplayRole:
            return self._values(key, col)[0]
        if role == SORT_ROLE:
            return self._values(key, col)[1]
        if role == Qt.ItemDataRole.TextAlignmentRole and col not in (C_NAME, C_STATE):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and col in (C_NAME, C_STATE):
            err = self.engine.error_text(key)
            return err or (self.engine.status[key].name if key in self.engine.status else None)
        if role == Qt.ItemDataRole.ForegroundRole and col == C_STATE:
            text = self.engine.state_text(key)
            if text == "Error":
                return QBrush(QColor(230, 80, 80))
        return None


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        progress = index.data(SORT_ROLE) or 0.0
        opt = QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(2, 3, -2, -3)
        opt.minimum, opt.maximum = 0, 1000
        opt.progress = int(progress * 1000)
        opt.text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        opt.textVisible = True
        opt.textAlignment = Qt.AlignmentFlag.AlignCenter
        opt.state = option.state | QStyle.StateFlag.State_Horizontal
        opt.palette = option.palette
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, opt, painter)


# ---------------------------------------------------------------------------
# Gráfico de velocidad
# ---------------------------------------------------------------------------

class SpeedGraph(QWidget):
    def __init__(self):
        super().__init__()
        self.samples: list[tuple[int, int]] = []
        self.title = "Global"
        self.setMinimumHeight(140)

    def set_data(self, samples, title: str) -> None:
        self.samples = list(samples)
        self.title = title
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        left, top, right, bottom = 70, 24, 12, 22
        w = self.width() - left - right
        h = self.height() - top - bottom
        if w < 20 or h < 20:
            return
        data = self.samples[-300:]
        peak = max([max(d, u) for d, u in data] + [10 * 1024])
        # escala «bonita»
        nice = 1024
        while nice < peak:
            nice *= 2
        peak = nice
        grid = QColor(pal.color(QPalette.ColorRole.Text))
        grid.setAlpha(40)
        p.setPen(QPen(grid, 1))
        font = p.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        p.setFont(font)
        for i in range(5):
            y = top + h * i / 4
            p.drawLine(QPointF(left, y), QPointF(left + w, y))
            p.save()
            p.setPen(pal.color(QPalette.ColorRole.Text))
            p.drawText(QRectF(0, y - 8, left - 6, 16), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       fmt_rate(peak * (4 - i) / 4))
            p.restore()
        p.setPen(pal.color(QPalette.ColorRole.Text))
        p.drawText(QRectF(left, top + h + 4, w, 16), int(Qt.AlignmentFlag.AlignLeft), "hace 5 min")
        p.drawText(QRectF(left, top + h + 4, w, 16), int(Qt.AlignmentFlag.AlignRight), "ahora")

        colors = [(QColor(60, 180, 90), 0, "Bajada"), (QColor(240, 150, 40), 1, "Subida")]
        n = 300
        for color, idx, _ in colors:
            if len(data) < 2:
                continue
            path = QPainterPath()
            offset = n - len(data)
            for i, sample in enumerate(data):
                x = left + w * (offset + i) / (n - 1)
                y = top + h - h * sample[idx] / peak
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(color, 2))
            p.drawPath(path)
        # leyenda
        x = left
        last = data[-1] if data else (0, 0)
        for color, idx, name in colors:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRect(QRectF(x, 6, 10, 10))
            p.setPen(pal.color(QPalette.ColorRole.Text))
            label = f"{name}: {fmt_speed(last[idx]) or '0 B/s'}"
            p.drawText(QRectF(x + 14, 2, 200, 18), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)
            x += 170
        p.drawText(QRectF(left, 2, w, 18), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self.title)
        p.end()


# ---------------------------------------------------------------------------
# Diálogos
# ---------------------------------------------------------------------------

class FolderPicker(QWidget):
    changed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(path)
        btn = QPushButton("Examinar…")
        btn.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)
        self.edit.textChanged.connect(self.changed)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.edit.text())
        if path:
            self.edit.setText(os.path.normpath(path))

    def path(self) -> str:
        return os.path.normpath(self.edit.text().strip())


class AddTorrentDialog(QDialog):
    """Elegir carpeta de destino y archivos a descargar."""

    def __init__(self, parent, ti: "lt.torrent_info", save_path: str, title: str = "Añadir torrent",
                 start_default: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        self.ti = ti
        fs = ti.files()
        self.sizes = [fs.file_size(i) for i in range(fs.num_files())]

        lay = QVBoxLayout(self)
        info = QLabel(f"<b>{ti.name()}</b><br>{fmt_size(ti.total_size())} · {fs.num_files()} archivo(s)"
                      + (" · <i>torrent privado</i>" if ti.priv() else ""))
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        form = QFormLayout()
        self.folder = FolderPicker(save_path)
        self.folder.changed.connect(self._update_space)
        form.addRow("Guardar en:", self.folder)
        lay.addLayout(form)

        bar = QHBoxLayout()
        b_all = QPushButton("Seleccionar todo")
        b_none = QPushButton("Ninguno")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        bar.addWidget(b_all)
        bar.addWidget(b_none)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Nombre", "Tamaño", "Prioridad"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setUniformRowHeights(True)
        lay.addWidget(self.tree, 1)
        self._populate(fs)
        self.tree.itemChanged.connect(lambda *_: self._update_space())

        self.space = QLabel()
        lay.addWidget(self.space)
        self.start = QCheckBox("Empezar la descarga inmediatamente")
        self.start.setChecked(start_default)
        lay.addWidget(self.start)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update_space()

    def _populate(self, fs) -> None:
        self.tree.blockSignals(True)
        folders: dict[str, QTreeWidgetItem] = {}
        self.file_items: list[tuple[int, QTreeWidgetItem]] = []
        flags_folder = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            parts = fs.file_path(i).replace("\\", "/").split("/")
            parent = None
            for depth in range(len(parts) - 1):
                k = "/".join(parts[: depth + 1])
                if k not in folders:
                    item = QTreeWidgetItem([parts[depth], "", ""])
                    item.setFlags(flags_folder)
                    item.setIcon(0, std_icon(self, "SP_DirIcon"))
                    (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
                    folders[k] = item
                parent = folders[k]
            item = QTreeWidgetItem([parts[-1], fmt_size(fs.file_size(i)), ""])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setIcon(0, std_icon(self, "SP_FileIcon"))
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            combo = QComboBox()
            for _, name in PRIORITIES[1:]:
                combo.addItem(name)
            combo.setCurrentIndex(1)
            self.tree.setItemWidget(item, 2, combo)
            self.file_items.append((i, item))
        for item in folders.values():
            item.setCheckState(0, Qt.CheckState.Checked)
        if len(folders) < 30:
            self.tree.expandAll()
        else:
            self.tree.expandToDepth(0)
        self.tree.resizeColumnToContents(1)
        self.tree.blockSignals(False)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)
        self._update_space()

    def priorities(self) -> list[int]:
        prios = [0] * len(self.sizes)
        prio_values = [p for p, _ in PRIORITIES[1:]]
        for i, item in self.file_items:
            if item.checkState(0) == Qt.CheckState.Checked:
                combo = self.tree.itemWidget(item, 2)
                prios[i] = prio_values[combo.currentIndex()] if combo else 4
        return prios

    def selected_size(self) -> int:
        return sum(self.sizes[i] for i, p in enumerate(self.priorities()) if p > 0)

    def _update_space(self) -> None:
        needed = self.selected_size()
        path = self.folder.path()
        probe = Path(path)
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            free = shutil.disk_usage(str(probe)).free
            ok = free >= needed
            color = "" if ok else "color:#e05050;"
            self.space.setText(f"<span style='{color}'>Seleccionado: <b>{fmt_size(needed)}</b> · "
                               f"Libre en disco: <b>{fmt_size(free)}</b>"
                               + ("" if ok else " — <b>no hay espacio suficiente</b>") + "</span>")
        except OSError:
            self.space.setText(f"Seleccionado: <b>{fmt_size(needed)}</b> · <span style='color:#e05050'>carpeta no accesible</span>")

    def _accept(self) -> None:
        if not any(p > 0 for p in self.priorities()):
            QMessageBox.warning(self, APP_NAME, "Selecciona al menos un archivo para descargar.")
            return
        if not self.folder.path():
            QMessageBox.warning(self, APP_NAME, "Elige una carpeta de destino.")
            return
        self.accept()


class MagnetDialog(QDialog):
    def __init__(self, parent, save_path: str, initial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Abrir enlaces magnet")
        self.resize(640, 300)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pega uno o varios enlaces magnet (uno por línea):"))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("magnet:?xt=urn:btih:…")
        self.text.setPlainText(initial)
        lay.addWidget(self.text, 1)
        form = QFormLayout()
        self.folder = FolderPicker(save_path)
        form.addRow("Guardar en:", self.folder)
        lay.addLayout(form)
        self.choose = QCheckBox("Elegir archivos cuando se reciban los metadatos")
        self.choose.setChecked(True)
        lay.addWidget(self.choose)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def links(self) -> list[str]:
        return [l.strip() for l in self.text.toPlainText().splitlines() if l.strip()]


class RemoveDialog(QDialog):
    def __init__(self, parent, names: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Eliminar torrents")
        lay = QVBoxLayout(self)
        shown = "<br>".join(f"• {n}" for n in names[:8]) + (f"<br>… y {len(names) - 8} más" if len(names) > 8 else "")
        lbl = QLabel(f"¿Eliminar {len(names)} torrent(s) de la lista?<br><br>{shown}")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self.delete_files = QCheckBox("Borrar también los archivos descargados del disco")
        lay.addWidget(self.delete_files)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Eliminar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


MBIT_TO_KIB = 1_000_000 / 8 / 1024   # 1 Mbit/s = 122,07 KiB/s


class SettingsDialog(QDialog):
    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.setWindowTitle("Ajustes")
        self.resize(640, 560)
        self.s = settings
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- General
        w = QWidget(); f = QFormLayout(w)
        self.theme = QComboBox(); self.theme.addItems(["Oscuro", "Claro"])
        self.theme.setCurrentIndex(0 if settings["theme"] == "oscuro" else 1)
        f.addRow("Tema:", self.theme)
        self.speed_unit = QComboBox()
        self.speed_unit.addItems(["Bytes (MiB/s)", "Bits (Mbps, como tu proveedor de internet)"])
        self.speed_unit.setCurrentIndex(1 if settings["speed_unit"] == "bits" else 0)
        f.addRow("Unidad de velocidad:", self.speed_unit)
        self.download_dir = FolderPicker(settings["download_dir"])
        f.addRow("Carpeta por defecto:", self.download_dir)
        self.ask_on_add = QCheckBox("Preguntar carpeta y archivos al añadir un torrent")
        self.ask_on_add.setChecked(settings["ask_on_add"])
        f.addRow(self.ask_on_add)
        self.start_paused = QCheckBox("Añadir los torrents en pausa")
        self.start_paused.setChecked(settings["start_paused"])
        f.addRow(self.start_paused)
        self.close_to_tray = QCheckBox("Al cerrar la ventana, seguir en la bandeja del sistema")
        self.close_to_tray.setChecked(settings["close_to_tray"])
        f.addRow(self.close_to_tray)
        self.minimize_to_tray = QCheckBox("Al minimizar, ocultar en la bandeja del sistema")
        self.minimize_to_tray.setChecked(settings["minimize_to_tray"])
        f.addRow(self.minimize_to_tray)
        self.start_minimized = QCheckBox("Iniciar minimizado")
        self.start_minimized.setChecked(settings["start_minimized"])
        f.addRow(self.start_minimized)
        self.assoc = QCheckBox("Abrir archivos .torrent y enlaces magnet con Corriente")
        self.assoc.setChecked(winassoc.is_registered())
        self.assoc.setEnabled(winassoc.available())
        f.addRow(self.assoc)
        btn_default = QPushButton("Abrir «Aplicaciones predeterminadas» de Windows…")
        btn_default.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:defaultapps")))
        f.addRow(btn_default)
        hint = QLabel("Si Windows sigue abriendo los .torrent con otro programa, elige Corriente una vez "
                      "en «Aplicaciones predeterminadas» (Windows protege esa elección).")
        hint.setWordWrap(True); hint.setStyleSheet("color: gray;")
        f.addRow(hint)
        tabs.addTab(w, "General")

        # --- Conexión
        w = QWidget(); f = QFormLayout(w)
        row = QHBoxLayout()
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(int(settings["listen_port"]))
        b_rand = QPushButton("Aleatorio")
        b_rand.clicked.connect(lambda: self.port.setValue(random_port()))
        row.addWidget(self.port); row.addWidget(b_rand); row.addStretch(1)
        f.addRow("Puerto de escucha:", row)
        self.upnp = QCheckBox("UPnP"); self.upnp.setChecked(settings["upnp"])
        self.natpmp = QCheckBox("NAT-PMP"); self.natpmp.setChecked(settings["natpmp"])
        r2 = QHBoxLayout(); r2.addWidget(self.upnp); r2.addWidget(self.natpmp); r2.addStretch(1)
        f.addRow("Redirección automática:", r2)
        self.iface = QComboBox()
        self.iface.addItem("Todas (automático)", "")
        current_iface = settings["network_interface"]
        found = False
        for it in netif.list_interfaces():
            label = f"{it['name']} ({it['ips'][0]})" + ("  — VPN" if it["vpn"] else "")
            self.iface.addItem(label, it["name"])
            if it["name"] == current_iface:
                found = True
        if current_iface and not found:
            self.iface.addItem(f"{current_iface} (no conectada)", current_iface)
        self.iface.setCurrentIndex(max(0, self.iface.findData(current_iface)))
        f.addRow("Interfaz de red:", self.iface)
        iface_hint = QLabel("Para usar Corriente fuera de la VPN: exclúyelo en el «split tunneling» de la VPN, "
                            "elige aquí tu adaptador físico (Ethernet o Wi-Fi) y reinicia Corriente.")
        iface_hint.setWordWrap(True); iface_hint.setStyleSheet("color: gray;")
        f.addRow(iface_hint)
        self.dht = QCheckBox("DHT"); self.dht.setChecked(settings["dht"])
        self.pex = QCheckBox("PeX (intercambio de pares)"); self.pex.setChecked(settings["pex"])
        self.lsd = QCheckBox("Descubrimiento local (LSD)"); self.lsd.setChecked(settings["lsd"])
        r3 = QHBoxLayout(); r3.addWidget(self.dht); r3.addWidget(self.pex); r3.addWidget(self.lsd); r3.addStretch(1)
        f.addRow("Búsqueda de pares:", r3)
        self.encryption = QComboBox()
        self.encryption.addItems(["Deshabilitado", "Habilitado (preferido)", "Forzado"])
        self.encryption.setCurrentIndex(["deshabilitado", "habilitado", "forzado"].index(settings["encryption"]))
        f.addRow("Cifrado del protocolo:", self.encryption)
        self.max_conn = QSpinBox(); self.max_conn.setRange(20, 10000); self.max_conn.setValue(settings["max_connections"])
        f.addRow("Conexiones totales:", self.max_conn)
        self.max_peers = QSpinBox(); self.max_peers.setRange(5, 2000); self.max_peers.setValue(settings["max_peers_per_torrent"])
        f.addRow("Pares por torrent:", self.max_peers)
        self.slots = QSpinBox(); self.slots.setRange(1, 100); self.slots.setValue(settings["upload_slots_per_torrent"])
        f.addRow("Ranuras de subida por torrent:", self.slots)
        tabs.addTab(w, "Conexión")

        # --- Velocidad
        w = QWidget(); f = QFormLayout(w)
        self.capacity = QDoubleSpinBox(); self.capacity.setRange(0, 100000); self.capacity.setDecimals(1)
        self.capacity.setSuffix(" Mbit/s")
        self.capacity.setValue(round(float(settings["upload_capacity_kbps"]) / MBIT_TO_KIB, 1))
        self.capacity.setSpecialValueText("Desconocida (sin límite)")
        f.addRow("Velocidad de subida de tu conexión:", self.capacity)
        self.percent = QSpinBox(); self.percent.setRange(1, 100); self.percent.setSuffix(" %")
        self.percent.setValue(settings["upload_percent"])
        f.addRow("Usar como máximo:", self.percent)
        self.limit_label = QLabel()
        f.addRow("Límite de subida resultante:", self.limit_label)
        self.dl_limit = QSpinBox(); self.dl_limit.setRange(0, 10_000_000); self.dl_limit.setSuffix(" KiB/s")
        self.dl_limit.setSpecialValueText("Ilimitada"); self.dl_limit.setValue(int(settings["download_limit_kbps"]))
        f.addRow("Límite de bajada:", self.dl_limit)
        note = QLabel("Dejar un 20 % de la subida libre evita que se sature tu conexión: si la subida va al "
                      "100 %, la descarga también se frena. Puedes medir tu subida en cualquier test de velocidad.")
        note.setWordWrap(True); note.setStyleSheet("color: gray;")
        f.addRow(note)
        self.capacity.valueChanged.connect(self._update_limit_label)
        self.percent.valueChanged.connect(self._update_limit_label)
        self._update_limit_label()
        tabs.addTab(w, "Velocidad")

        # --- Cola
        w = QWidget(); f = QFormLayout(w)
        self.active_limit = QSpinBox(); self.active_limit.setRange(1, 1000); self.active_limit.setValue(settings["active_limit"])
        f.addRow("Torrents activos como máximo:", self.active_limit)
        self.active_dl = QSpinBox(); self.active_dl.setRange(1, 1000); self.active_dl.setValue(settings["active_downloads"])
        f.addRow("Descargas activas como máximo:", self.active_dl)
        self.active_seeds = QSpinBox(); self.active_seeds.setRange(0, 1000); self.active_seeds.setValue(settings["active_seeds"])
        f.addRow("Semillas activas como máximo:", self.active_seeds)
        note = QLabel("Los torrents lentos (sin actividad) no cuentan para estos límites, así que no bloquean la cola.")
        note.setWordWrap(True); note.setStyleSheet("color: gray;")
        f.addRow(note)
        tabs.addTab(w, "Cola")

        # --- Disco
        w = QWidget(); f = QFormLayout(w)
        self.preallocate = QCheckBox("Preasignar el espacio completo al añadir (reduce fragmentación en discos duros)")
        self.preallocate.setChecked(settings["preallocate"])
        f.addRow(self.preallocate)
        self.disk_threads = QSpinBox(); self.disk_threads.setRange(1, 64); self.disk_threads.setValue(settings["disk_threads"])
        f.addRow("Hilos de E/S de disco:", self.disk_threads)
        self.hash_threads = QSpinBox(); self.hash_threads.setRange(1, 32); self.hash_threads.setValue(settings["hashing_threads"])
        f.addRow("Hilos de verificación:", self.hash_threads)
        self.send_buf = QSpinBox(); self.send_buf.setRange(1, 64); self.send_buf.setSuffix(" MiB"); self.send_buf.setValue(settings["send_buffer_mb"])
        f.addRow("Búfer de envío:", self.send_buf)
        self.file_pool = QSpinBox(); self.file_pool.setRange(20, 5000); self.file_pool.setValue(settings["file_pool_size"])
        f.addRow("Archivos abiertos a la vez:", self.file_pool)
        note = QLabel("En SSD lo mejor es no preasignar (archivos dispersos). En discos duros mecánicos, "
                      "preasignar evita fragmentación pero tarda más al añadir.")
        note.setWordWrap(True); note.setStyleSheet("color: gray;")
        f.addRow(note)
        tabs.addTab(w, "Disco")

        # --- Trackers
        w = QWidget(); v = QVBoxLayout(w)
        self.add_trackers = QCheckBox("Añadir automáticamente estos trackers públicos a los torrents nuevos")
        self.add_trackers.setChecked(settings["add_public_trackers"])
        v.addWidget(self.add_trackers)
        lbl = QLabel("Nunca se añaden a torrents privados. Un tracker por línea.")
        lbl.setStyleSheet("color: gray;")
        v.addWidget(lbl)
        self.trackers = QPlainTextEdit("\n".join(settings["public_trackers"]))
        v.addWidget(self.trackers, 1)
        tabs.addTab(w, "Trackers")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _update_limit_label(self):
        cap = self.capacity.value() * MBIT_TO_KIB
        if cap <= 0:
            self.limit_label.setText("Sin límite (indica la subida de tu conexión para limitarla)")
        else:
            self.limit_label.setText(f"{cap * self.percent.value() / 100:.0f} KiB/s")

    def values(self) -> dict:
        return {
            "theme": "oscuro" if self.theme.currentIndex() == 0 else "claro",
            "speed_unit": "bits" if self.speed_unit.currentIndex() == 1 else "bytes",
            "download_dir": self.download_dir.path(),
            "ask_on_add": self.ask_on_add.isChecked(),
            "start_paused": self.start_paused.isChecked(),
            "close_to_tray": self.close_to_tray.isChecked(),
            "minimize_to_tray": self.minimize_to_tray.isChecked(),
            "start_minimized": self.start_minimized.isChecked(),
            "file_association": self.assoc.isChecked(),
            "listen_port": self.port.value(),
            "network_interface": self.iface.currentData() or "",
            "upnp": self.upnp.isChecked(), "natpmp": self.natpmp.isChecked(),
            "dht": self.dht.isChecked(), "pex": self.pex.isChecked(), "lsd": self.lsd.isChecked(),
            "encryption": ["deshabilitado", "habilitado", "forzado"][self.encryption.currentIndex()],
            "max_connections": self.max_conn.value(),
            "max_peers_per_torrent": self.max_peers.value(),
            "upload_slots_per_torrent": self.slots.value(),
            "upload_capacity_kbps": round(self.capacity.value() * MBIT_TO_KIB),
            "upload_percent": self.percent.value(),
            "download_limit_kbps": self.dl_limit.value(),
            "active_limit": self.active_limit.value(),
            "active_downloads": self.active_dl.value(),
            "active_seeds": self.active_seeds.value(),
            "preallocate": self.preallocate.isChecked(),
            "disk_threads": self.disk_threads.value(),
            "hashing_threads": self.hash_threads.value(),
            "send_buffer_mb": self.send_buf.value(),
            "file_pool_size": self.file_pool.value(),
            "add_public_trackers": self.add_trackers.isChecked(),
            "public_trackers": [l.strip() for l in self.trackers.toPlainText().splitlines() if l.strip()],
        }


class DiagnosisDialog(QDialog):
    def __init__(self, parent, name: str, results: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("¿Por qué va lento?")
        self.resize(620, 480)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        colors = {"alto": "#e05050", "medio": "#e0a030", "info": "#4a9ee0"}
        labels = {"alto": "Causa probable", "medio": "Puede influir", "info": "Información"}
        html = [f"<h3>{name}</h3>"]
        for r in results:
            html.append(
                f"<p><span style='color:{colors[r['nivel']]}; font-weight:bold'>● {labels[r['nivel']]}:</span> "
                f"<b>{r['titulo']}</b><br>{r['detalle']}</p>")
        view.setHtml("".join(html))
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Cerrar")
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, engine: TorrentEngine, settings: Settings):
        super().__init__()
        self.engine = engine
        self.settings = settings
        self._quitting = False
        self._selection_queue: list[str] = []
        self._selection_open = False
        self._detail_files_key = None
        set_speed_unit(settings["speed_unit"])

        self.setWindowTitle(APP_NAME)
        self.app_icon = make_app_icon()
        self.setWindowIcon(self.app_icon)
        self.resize(1200, 720)
        self.setAcceptDrops(True)

        self._build_actions()
        self._build_ui()
        self._build_tray()
        self._restore_state()

        self.alert_timer = QTimer(self, interval=300, timeout=self._on_alert_tick)
        self.alert_timer.start()
        self.ui_timer = QTimer(self, interval=1000, timeout=self._on_ui_tick)
        self.ui_timer.start()

    # ------------------------------------------------------------- construcción
    def _act(self, text, slot, icon=None, shortcut=None, tip=None) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(std_icon(self, icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        self.a_open = self._act("Abrir torrent…", self.open_torrent_dialog, "SP_DialogOpenButton", "Ctrl+O",
                                "Abrir uno o varios archivos .torrent")
        self.a_magnet = self._act("Abrir magnet…", self.open_magnet_dialog, "SP_FileLinkIcon", "Ctrl+M",
                                  "Pegar enlaces magnet")
        self.a_resume = self._act("Reanudar", lambda: self._for_selected(self.engine.resume), "SP_MediaPlay", None,
                                  "Reanudar los torrents seleccionados")
        self.a_force = self._act("Forzar inicio", lambda: self._for_selected(lambda k: self.engine.resume(k, force=True)),
                                 None, None, "Ignorar la cola e iniciar ya")
        self.a_pause = self._act("Pausar", lambda: self._for_selected(self.engine.pause), "SP_MediaPause", None,
                                 "Pausar los torrents seleccionados")
        self.a_remove = self._act("Eliminar", self.remove_selected, "SP_TrashIcon", "Delete",
                                  "Quitar de la lista (opcionalmente borrar archivos)")
        self.a_recheck = self._act("Forzar recomprobación", self.recheck_selected, "SP_BrowserReload", None,
                                   "Verificar de nuevo los datos descargados")
        self.a_top = self._act("Mover arriba del todo", lambda: self._queue("arriba"), "SP_ArrowUp")
        self.a_up = self._act("Subir prioridad", lambda: self._queue("subir"), "SP_ArrowUp", "Ctrl+Up",
                              "Subir en la cola (más prioridad)")
        self.a_down = self._act("Bajar prioridad", lambda: self._queue("bajar"), "SP_ArrowDown", "Ctrl+Down",
                                "Bajar en la cola (menos prioridad)")
        self.a_bottom = self._act("Mover abajo del todo", lambda: self._queue("abajo"), "SP_ArrowDown")
        self.a_folder = self._act("Abrir carpeta", self.open_folder, "SP_DirOpenIcon")
        self.a_copy_magnet = self._act("Copiar enlace magnet", self.copy_magnet)
        self.a_add_trackers = self._act("Añadir trackers públicos", self.add_public_trackers, None, None,
                                        "Añadir la lista de trackers públicos de Ajustes a los torrents seleccionados")
        self.a_slow = self._act("¿Por qué va lento?", self.why_slow, "SP_MessageBoxQuestion", "F1",
                                "Analizar el torrent seleccionado")
        self.a_settings = self._act("Ajustes…", self.open_settings, "SP_FileDialogDetailedView", "Ctrl+,")
        self.a_quit = self._act("Salir", self.quit_app, None, "Ctrl+Q")
        self.a_theme = self._act("Cambiar tema claro/oscuro", self.toggle_theme)
        self.a_bits = self._act("Velocidad en Mbps (bits)", self.toggle_speed_unit)
        self.a_bits.setCheckable(True)
        self.a_bits.setChecked(self.settings["speed_unit"] == "bits")
        self.a_pause_all = self._act("Pausar todo", lambda: [self.engine.pause(k) for k in list(self.engine.handles)])
        self.a_resume_all = self._act("Reanudar todo", lambda: [self.engine.resume(k) for k in list(self.engine.handles)])

    def _build_ui(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&Archivo")
        for a in (self.a_open, self.a_magnet, None, self.a_settings, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Torrent")
        for a in (self.a_resume, self.a_force, self.a_pause, self.a_remove, self.a_recheck, None,
                  self.a_top, self.a_up, self.a_down, self.a_bottom, None, self.a_folder, self.a_copy_magnet,
                  self.a_add_trackers, None, self.a_pause_all, self.a_resume_all):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Ver")
        m.addAction(self.a_theme)
        m.addAction(self.a_bits)
        m = mb.addMenu("A&yuda")
        m.addAction(self.a_slow)
        m.addAction(self._act("Acerca de", self.about))

        tb = QToolBar("Principal")
        tb.setObjectName("toolbar")
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for a in (self.a_open, self.a_magnet, None, self.a_resume, self.a_pause, self.a_remove, None,
                  self.a_up, self.a_down, None, self.a_slow, self.a_settings):
            tb.addSeparator() if a is None else tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por nombre…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        tb.addWidget(self.search)
        self.addToolBar(tb)

        # Lista
        self.model = TorrentModel(self.engine)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setFilterKeyColumn(C_NAME)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_QUEUE, Qt.SortOrder.AscendingOrder)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setItemDelegateForColumn(C_PROGRESS, ProgressDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._torrent_menu)
        self.table.doubleClicked.connect(lambda *_: self.open_folder())
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setStretchLastSection(False)
        widths = {C_QUEUE: 36, C_SIZE: 90, C_PROGRESS: 130, C_STATE: 140, C_SEEDS: 80, C_PEERS: 80,
                  C_DOWN: 95, C_UP: 95, C_ETA: 90, C_RATIO: 55}
        for c, wdt in widths.items():
            self.table.setColumnWidth(c, wdt)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._refresh_details(force=True))

        # Detalles
        self.details = QTabWidget()

        self.general = QTextBrowser()
        self.details.addTab(self.general, "General")

        self.files_tree = QTreeWidget()
        self.files_tree.setHeaderLabels(["Archivo", "Tamaño", "Progreso", "Prioridad"])
        self.files_tree.setRootIsDecorated(False)
        self.files_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files_tree.customContextMenuRequested.connect(self._files_menu)
        self.details.addTab(self.files_tree, "Archivos")

        self.peers_table = QTableWidget(0, 7)
        self.peers_table.setHorizontalHeaderLabels(["IP", "Cliente", "Indicadores", "Progreso", "Bajada", "Subida", "Descargado"])
        self.peers_table.verticalHeader().hide()
        self.peers_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.peers_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.peers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.peers_table.setToolTip("Indicadores: E=entrante, C=cifrado, D=DHT, X=PeX, L=LSD, S=desatendido, Sm=semilla")
        self.details.addTab(self.peers_table, "Pares")

        self.trackers_table = QTableWidget(0, 5)
        self.trackers_table.setHorizontalHeaderLabels(["URL", "Estado", "Semillas", "Pares", "Mensaje"])
        self.trackers_table.verticalHeader().hide()
        self.trackers_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.trackers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.trackers_table.setColumnWidth(1, 130)
        self.trackers_table.setColumnWidth(4, 220)
        self.details.addTab(self.trackers_table, "Trackers")

        self.graph = SpeedGraph()
        self.details.addTab(self.graph, "Velocidad")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.details.addTab(self.log, "Registro")
        self.details.currentChanged.connect(lambda *_: self._refresh_details(force=True))

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self.splitter)

        # Barra de estado
        sb = self.statusBar()
        self.lbl_port = QLabel()
        self.lbl_dht = QLabel()
        self.lbl_speed = QLabel()
        for w in (self.lbl_port, self.lbl_dht, self.lbl_speed):
            w.setContentsMargins(8, 0, 8, 0)
            sb.addPermanentWidget(w)
        self._port_icons = {
            "abierto": dot_pixmap(QColor(60, 190, 90)),
            "probable": dot_pixmap(QColor(140, 200, 80)),
            "cerrado": dot_pixmap(QColor(220, 70, 70)),
            "comprobando": dot_pixmap(QColor(200, 170, 60)),
        }

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction(self._act("Mostrar / ocultar", self.toggle_visible))
        menu.addSeparator()
        menu.addAction(self.a_open)
        menu.addAction(self.a_magnet)
        menu.addSeparator()
        menu.addAction(self.a_pause_all)
        menu.addAction(self.a_resume_all)
        menu.addSeparator()
        menu.addAction(self.a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _restore_state(self) -> None:
        from PySide6.QtCore import QByteArray
        for key, fn in (("window_geometry", self.restoreGeometry), ("window_state", self.restoreState),
                        ("splitter_state", self.splitter.restoreState),
                        ("header_state", self.table.horizontalHeader().restoreState)):
            val = self.settings[key]
            if val:
                try:
                    fn(QByteArray.fromBase64(val.encode()))
                except Exception:
                    pass

    def _save_state(self) -> None:
        self.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.settings["window_state"] = bytes(self.saveState().toBase64()).decode()
        self.settings["splitter_state"] = bytes(self.splitter.saveState().toBase64()).decode()
        self.settings["header_state"] = bytes(self.table.horizontalHeader().saveState().toBase64()).decode()
        self.settings.save()

    # ------------------------------------------------------------- utilidades
    def selected_keys(self) -> list[str]:
        rows = self.table.selectionModel().selectedRows()
        return [self.proxy.data(r, KEY_ROLE) for r in rows]

    def current_key(self) -> str | None:
        idx = self.table.currentIndex()
        if idx.isValid() and self.table.selectionModel().isRowSelected(idx.row(), QModelIndex()):
            return self.proxy.data(idx, KEY_ROLE)
        keys = self.selected_keys()
        return keys[0] if keys else None

    def _for_selected(self, fn) -> None:
        for k in self.selected_keys():
            fn(k)

    def log_msg(self, text: str) -> None:
        self.log.appendPlainText(time.strftime("[%H:%M:%S] ") + text)

    def notify(self, title: str, text: str, error: bool = False) -> None:
        self.log_msg(f"{title}: {text}")
        if self.isVisible() and not self.isMinimized():
            self.statusBar().showMessage(f"{title}: {text}", 10000)
        if self.tray.isVisible() and (not self.isVisible() or self.isMinimized()):
            icon = QSystemTrayIcon.MessageIcon.Critical if error else QSystemTrayIcon.MessageIcon.Information
            self.tray.showMessage(title, text, icon, 8000)

    def error_box(self, text: str) -> None:
        self.log_msg("Error: " + text.replace("\n", " "))
        QMessageBox.critical(self, APP_NAME, text)

    # ------------------------------------------------------------- añadir
    def handle_args(self, args: list[str]) -> None:
        """Procesa archivos/enlaces recibidos por línea de comandos o desde otra instancia."""
        for arg in args:
            arg = arg.strip().strip('"')
            if not arg:
                continue
            if arg.lower().startswith("magnet:"):
                self.add_magnets([arg], self.settings["download_dir"], self.settings["ask_on_add"])
            elif arg.lower().endswith(".torrent"):
                self.add_torrent_file(arg)
            else:
                self.log_msg(f"Argumento ignorado: {arg}")

    def open_torrent_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Abrir torrent", self.settings["download_dir"],
                                                "Archivos torrent (*.torrent);;Todos los archivos (*)")
        for p in paths:
            self.add_torrent_file(p)

    def add_torrent_file(self, path: str) -> None:
        try:
            ti = self.engine.load_torrent_info(path)
        except TorrentError as exc:
            self.error_box(str(exc))
            return
        save_path = self.settings["download_dir"]
        prios = None
        start = not self.settings["start_paused"]
        if self.settings["ask_on_add"]:
            self.show_window()
            dlg = AddTorrentDialog(self, ti, save_path, start_default=start)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            save_path, prios, start = dlg.folder.path(), dlg.priorities(), dlg.start.isChecked()
        try:
            self.engine.add_torrent_file(ti, save_path, prios, paused=not start)
            self.log_msg(f"Añadido: {ti.name()}")
        except TorrentError as exc:
            self.error_box(str(exc))

    def open_magnet_dialog(self, initial: str = "") -> None:
        if not initial:
            clip = QGuiApplication.clipboard().text().strip()
            if clip.lower().startswith("magnet:?"):
                initial = clip
        dlg = MagnetDialog(self, self.settings["download_dir"], initial)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.add_magnets(dlg.links(), dlg.folder.path(), dlg.choose.isChecked())

    def add_magnets(self, links: list[str], save_path: str, choose: bool) -> None:
        errors = []
        for link in links:
            try:
                self.engine.add_magnet(link, save_path, await_selection=choose,
                                       paused=self.settings["start_paused"])
                self.log_msg(f"Magnet añadido: {link[:80]}…")
            except TorrentError as exc:
                errors.append(str(exc))
        if errors:
            self.error_box("\n\n".join(errors))

    def _process_selection_queue(self) -> None:
        if self._selection_open or not self._selection_queue:
            return
        key = self._selection_queue.pop(0)
        h = self.engine.handles.get(key)
        meta = self.engine.meta.get(key)
        if not h or not h.is_valid() or not meta or not meta.pending_selection:
            return
        ti = h.torrent_file()
        if ti is None:
            return
        self._selection_open = True
        try:
            self.show_window()
            dlg = AddTorrentDialog(self, ti, h.status().save_path, "Elegir archivos del magnet",
                                   start_default=not self.settings["start_paused"])
            if dlg.exec() == QDialog.DialogCode.Accepted:
                try:
                    self.engine.confirm_selection(key, dlg.folder.path(), dlg.priorities(), dlg.start.isChecked())
                except TorrentError as exc:
                    self.error_box(str(exc))
                    self._selection_queue.append(key)
            else:
                if QMessageBox.question(self, APP_NAME, f"¿Quitar «{ti.name()}» de la lista?") == QMessageBox.StandardButton.Yes:
                    self.engine.remove(key, False)
                else:
                    self.log_msg(f"«{ti.name()}» sigue esperando selección (clic derecho → Elegir archivos).")
        finally:
            self._selection_open = False
        QTimer.singleShot(0, self._process_selection_queue)

    # ------------------------------------------------------------- acciones
    def remove_selected(self) -> None:
        keys = self.selected_keys()
        if not keys:
            return
        names = [self.engine.status[k].name if k in self.engine.status else k for k in keys]
        dlg = RemoveDialog(self, names)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for k in keys:
                self.engine.remove(k, dlg.delete_files.isChecked())
            self.model.sync()

    def recheck_selected(self) -> None:
        keys = self.selected_keys()
        if keys and QMessageBox.question(
                self, APP_NAME, f"¿Volver a comprobar {len(keys)} torrent(s)? Puede tardar con archivos grandes.") \
                == QMessageBox.StandardButton.Yes:
            self._for_selected(self.engine.recheck)

    def _queue(self, where: str) -> None:
        keys = self.selected_keys()
        if where in ("subir", "abajo"):
            keys = list(keys)
        else:
            keys = list(reversed(keys))
        for k in keys:
            self.engine.queue(k, where)

    def open_folder(self) -> None:
        key = self.current_key()
        if not key:
            return
        path = self.engine.open_folder_path(key)
        if not path:
            return
        if os.path.isfile(path) and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def copy_magnet(self) -> None:
        key = self.current_key()
        h = self.engine.handles.get(key) if key else None
        if h and h.is_valid():
            QGuiApplication.clipboard().setText(lt.make_magnet_uri(h))
            self.statusBar().showMessage("Enlace magnet copiado al portapapeles", 4000)

    def add_public_trackers(self) -> None:
        keys = self.selected_keys()
        if not keys:
            QMessageBox.information(self, APP_NAME, "Selecciona primero uno o varios torrents.")
            return
        total, notes = 0, []
        for k in keys:
            added, reason = self.engine.add_public_trackers(k)
            total += added
            name = self.engine.status[k].name if k in self.engine.status else k
            self.log_msg(f"Trackers públicos en «{name}»: {added} añadidos" + (f" ({reason})" if reason else ""))
            if reason and len(keys) == 1:
                notes.append(reason)
        if total:
            self.statusBar().showMessage(f"Se añadieron {total} trackers públicos. Anunciando…", 6000)
            self._refresh_details(force=True)
        else:
            QMessageBox.information(self, APP_NAME, "No se añadió ningún tracker.\n\n"
                                    + ("\n".join(notes) or "Revisa la pestaña Registro para ver el motivo."))

    def why_slow(self) -> None:
        key = self.current_key()
        if not key:
            QMessageBox.information(self, APP_NAME, "Selecciona primero un torrent de la lista.")
            return
        interfaces = [i.humanReadableName() for i in QNetworkInterface.allInterfaces()
                      if i.flags() & QNetworkInterface.InterfaceFlag.IsUp
                      and not i.flags() & QNetworkInterface.InterfaceFlag.IsLoopBack]
        results = self.engine.diagnose(key, interfaces)
        name = self.engine.status[key].name if key in self.engine.status else key
        DiagnosisDialog(self, name, results).exec()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        values = dlg.values()
        old_theme = self.settings["theme"]
        assoc_wanted = values["file_association"]
        self.settings.update(values)
        try:
            if assoc_wanted and not winassoc.is_registered():
                winassoc.register()
                self.log_msg("Asociación de .torrent y magnet: activada.")
            elif not assoc_wanted and winassoc.is_registered():
                winassoc.unregister()
                self.log_msg("Asociación de .torrent y magnet: desactivada.")
        except OSError as exc:
            self.error_box(f"No se pudo cambiar la asociación de archivos:\n{exc}")
        self.settings.save()
        self.engine.apply_settings()
        if values["theme"] != old_theme:
            apply_theme(QApplication.instance(), values["theme"])
        set_speed_unit(self.settings["speed_unit"])
        self.a_bits.setChecked(self.settings["speed_unit"] == "bits")
        self.statusBar().showMessage("Ajustes guardados y aplicados", 4000)

    def toggle_speed_unit(self) -> None:
        self.settings["speed_unit"] = "bits" if self.a_bits.isChecked() else "bytes"
        set_speed_unit(self.settings["speed_unit"])
        self.settings.save()
        self._on_ui_tick()

    def toggle_theme(self) -> None:
        self.settings["theme"] = "claro" if self.settings["theme"] == "oscuro" else "oscuro"
        apply_theme(QApplication.instance(), self.settings["theme"])
        self.settings.save()

    def about(self) -> None:
        QMessageBox.about(self, f"Acerca de {APP_NAME}",
                          f"<b>{APP_NAME} {APP_VERSION}</b><br>Cliente BitTorrent de escritorio.<br><br>"
                          f"libtorrent {lt.__version__} · PySide6<br>Datos: {self.settings.path.parent}")

    # ------------------------------------------------------------- menús contextuales
    def _torrent_menu(self, pos) -> None:
        keys = self.selected_keys()
        if not keys:
            return
        menu = QMenu(self)
        for a in (self.a_resume, self.a_force, self.a_pause, None, self.a_remove, self.a_recheck, None):
            menu.addSeparator() if a is None else menu.addAction(a)
        pending = [k for k in keys if self.engine.meta.get(k) and self.engine.meta[k].pending_selection]
        if pending:
            menu.addAction("Elegir archivos…", lambda: self._request_selection(pending))
        sub = menu.addMenu("Prioridad en la cola")
        for a in (self.a_top, self.a_up, self.a_down, self.a_bottom):
            sub.addAction(a)
        menu.addSeparator()
        menu.addAction(self.a_folder)
        menu.addAction(self.a_copy_magnet)
        menu.addAction(self.a_add_trackers)
        menu.addSeparator()
        menu.addAction(self.a_slow)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _request_selection(self, keys: list[str]) -> None:
        for k in keys:
            h = self.engine.handles.get(k)
            if h and h.is_valid() and h.status().has_metadata:
                if k not in self._selection_queue:
                    self._selection_queue.append(k)
            else:
                self.statusBar().showMessage("Aún no se han recibido los metadatos de ese magnet.", 5000)
        self._process_selection_queue()

    def _files_menu(self, pos) -> None:
        key = self.current_key()
        items = self.files_tree.selectedItems()
        if not key or not items:
            return
        indices = [it.data(0, Qt.ItemDataRole.UserRole) for it in items]
        menu = QMenu(self)
        sub = menu.addMenu("Prioridad")
        for value, name in PRIORITIES:
            sub.addAction(name, lambda v=value: (self.engine.set_file_priority(key, indices, v),
                                                 self._refresh_details(force=True)))
        h = self.engine.handles.get(key)
        if len(items) == 1 and h:
            st = self.engine.status.get(key)
            path = Path(st.save_path) / items[0].data(1, Qt.ItemDataRole.UserRole) if st else None
            if path and path.exists():
                menu.addAction("Abrir", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        menu.exec(self.files_tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------- temporizadores
    def _on_alert_tick(self) -> None:
        for ev in self.engine.poll():
            kind = ev[0]
            if kind == "needs_selection":
                if ev[1] not in self._selection_queue:
                    self._selection_queue.append(ev[1])
                QTimer.singleShot(0, self._process_selection_queue)
            elif kind == "finished":
                self.notify("Descarga completada", ev[2])
            elif kind == "error":
                self.notify("Error", ev[2], error=True)
            elif kind == "log":
                self.log_msg(ev[1])
            elif kind == "metadata":
                st = self.engine.status.get(ev[1])
                self.log_msg(f"Metadatos recibidos: {st.name if st else ev[1]}")

    def _on_ui_tick(self) -> None:
        self.engine.refresh()
        self.model.sync()
        self._refresh_status_bar()
        self._refresh_details()

    def _refresh_status_bar(self) -> None:
        state, text = self.engine.port_status()
        names = {"abierto": "Puerto abierto", "probable": "Puerto redirigido",
                 "cerrado": "Puerto cerrado", "comprobando": "Puerto: comprobando"}
        self.lbl_port.setText(f"● {names[state]} ({self.engine.listen_port})")
        color = {"abierto": "#3cbe5a", "probable": "#8cc850", "cerrado": "#dc4646", "comprobando": "#c8aa3c"}[state]
        self.lbl_port.setStyleSheet(f"color: {color};")
        self.lbl_port.setToolTip(text)
        if self.settings["dht"]:
            self.lbl_dht.setText(f"DHT: {self.engine.dht_nodes} nodos")
        else:
            self.lbl_dht.setText("DHT: desactivado")
        if self.settings["network_interface"]:
            self.lbl_dht.setText(self.lbl_dht.text() + f"  ·  Red: {self.settings['network_interface']}")
        down, up = self.engine.global_history[-1] if self.engine.global_history else (0, 0)
        limit = self.settings.effective_upload_limit_bytes()
        up_lim = f" [{fmt_rate(limit)}]" if limit else ""
        self.lbl_speed.setText(f"↓ {fmt_rate(down)}   ↑ {fmt_rate(up)}{up_lim}")
        self.tray.setToolTip(f"{APP_NAME}\n↓ {fmt_rate(down)}  ↑ {fmt_rate(up)}")

    def _refresh_details(self, force: bool = False) -> None:
        if not self.details.isVisible() and not force:
            return
        key = self.current_key()
        tab = self.details.currentWidget()
        if tab is self.graph:
            if key and key in self.engine.meta:
                name = self.engine.status[key].name if key in self.engine.status else ""
                self.graph.set_data(self.engine.meta[key].history, name[:50])
            else:
                self.graph.set_data(self.engine.global_history, "Global (todos los torrents)")
            return
        if tab is self.log:
            return
        if not key:
            self.general.setHtml("<p style='color:gray'>Selecciona un torrent para ver sus detalles.</p>")
            self.files_tree.clear()
            self._detail_files_key = None
            self.peers_table.setRowCount(0)
            self.trackers_table.setRowCount(0)
            return
        if tab is self.general:
            self._fill_general(key)
        elif tab is self.files_tree:
            self._fill_files(key, force)
        elif tab is self.peers_table:
            self._fill_table(self.peers_table, [
                [p["ip"], p["client"], p["flags"], f"{p['progress'] * 100:.1f} %", fmt_speed(p["down"]),
                 fmt_speed(p["up"]), fmt_size(p["downloaded"])] for p in self.engine.peers(key)])
        elif tab is self.trackers_table:
            self._fill_table(self.trackers_table, [
                [t["url"], t["state"], "" if t["seeds"] < 0 else str(t["seeds"]),
                 "" if t["peers"] < 0 else str(t["peers"]), t["message"]] for t in self.engine.trackers(key)])

    def _fill_general(self, key: str) -> None:
        st = self.engine.status.get(key)
        if st is None:
            return
        h = self.engine.handles.get(key)
        ti = h.torrent_file() if h and h.is_valid() else None
        down = st.all_time_download
        rows = [
            ("Estado", self.engine.state_text(key)),
            ("Guardado en", st.save_path),
            ("Tamaño seleccionado", fmt_size(st.total_wanted) if st.has_metadata else "—"),
            ("Completado", f"{fmt_size(st.total_wanted_done)} ({st.progress * 100:.2f} %)"),
            ("Descargado / Subido", f"{fmt_size(st.all_time_download)} / {fmt_size(st.all_time_upload)}"),
            ("Ratio", f"{(st.all_time_upload / down if down else 0):.2f}"),
            ("Conexiones", f"{st.num_connections} (semillas {st.num_seeds}, pares {st.num_peers - st.num_seeds})"),
            ("Disponibilidad", f"{max(st.distributed_copies, 0):.2f} copias"),
            ("Datos descartados (hash)", fmt_size(st.total_failed_bytes)),
            ("Info-hash", key),
        ]
        if ti is not None:
            rows.append(("Piezas", f"{ti.num_pieces()} × {fmt_size(ti.piece_length())}"))
            rows.append(("Privado", "Sí" if ti.priv() else "No"))
            if ti.comment():
                rows.append(("Comentario", ti.comment()))
        err = self.engine.error_text(key)
        if err:
            rows.insert(1, ("<span style='color:#e05050'>Error</span>", f"<span style='color:#e05050'>{err}</span>"))
        html = "<table cellspacing='4'>" + "".join(
            f"<tr><td style='color:gray; padding-right:12px'>{k}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"
        sb = self.general.verticalScrollBar().value()
        self.general.setHtml(html)
        self.general.verticalScrollBar().setValue(sb)

    def _fill_files(self, key: str, force: bool) -> None:
        files = self.engine.files(key)
        tree = self.files_tree
        if self._detail_files_key != key or tree.topLevelItemCount() != len(files):
            tree.clear()
            for f in files:
                it = QTreeWidgetItem([f["path"], fmt_size(f["size"]), "", ""])
                it.setData(0, Qt.ItemDataRole.UserRole, f["index"])
                it.setData(1, Qt.ItemDataRole.UserRole, f["path"])
                it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tree.addTopLevelItem(it)
            self._detail_files_key = key
            tree.resizeColumnToContents(1)
        for i, f in enumerate(files):
            it = tree.topLevelItem(i)
            it.setText(2, f"{f['progress'] * 100:.1f} %")
            it.setText(3, prio_name(f["priority"]))
            it.setForeground(0, self.palette().color(QPalette.ColorRole.PlaceholderText) if f["priority"] == 0
                             else self.palette().color(QPalette.ColorRole.Text))
        if not files:
            tree.clear()
            self._detail_files_key = None

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = table.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(r, c, item)
                if item.text() != val:
                    item.setText(val)
        table.setSortingEnabled(True)

    # ------------------------------------------------------------- ventana / bandeja
    def show_window(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_window()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_visible()

    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and self.settings["minimize_to_tray"] and QSystemTrayIcon.isSystemTrayAvailable()):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if not self._quitting and self.settings["close_to_tray"] and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            if not self.settings.get("tray_hint_shown"):
                self.tray.showMessage(APP_NAME, "Sigo funcionando en la bandeja del sistema. "
                                                "Usa «Salir» para cerrar del todo.",
                                      QSystemTrayIcon.MessageIcon.Information, 5000)
                self.settings["tray_hint_shown"] = True
            return
        event.accept()
        self.quit_app()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.alert_timer.stop()
        self.ui_timer.stop()
        self.statusBar().showMessage("Guardando estado de los torrents…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self._save_state()
        except Exception:
            pass
        try:
            self.engine.shutdown(timeout=10)
        finally:
            QApplication.restoreOverrideCursor()
            self.tray.hide()
            QApplication.quit()

    # ------------------------------------------------------------- arrastrar y soltar
    def dragEnterEvent(self, event) -> None:
        md = event.mimeData()
        if md.hasUrls() and any(u.toLocalFile().lower().endswith(".torrent") for u in md.urls()):
            event.acceptProposedAction()
        elif md.hasText() and "magnet:?" in md.text():
            event.acceptProposedAction()
        elif md.hasUrls() and any(u.scheme() == "magnet" for u in md.urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        items = []
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile():
                    items.append(u.toLocalFile())
                elif u.scheme() == "magnet":
                    items.append(u.toString())
        if not items and md.hasText():
            items = [l for l in md.text().split() if l.startswith("magnet:?")]
        event.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.handle_args(items))


def first_run(window: MainWindow, settings: Settings) -> None:
    """Pregunta la velocidad de subida y la asociación de archivos la primera vez."""
    if settings["first_run_done"]:
        return
    mbit, ok = QInputDialog.getDouble(
        window, f"Bienvenido a {APP_NAME}",
        "¿Cuál es la velocidad de SUBIDA de tu conexión (Mbit/s)?\n\n"
        "Se usará el 80 % como límite para que tu conexión no se sature.\n"
        "Si no la sabes, deja 0 (sin límite) y cámbiala luego en Ajustes → Velocidad.",
        0.0, 0.0, 100000.0, 1)
    if ok and mbit > 0:
        settings["upload_capacity_kbps"] = round(mbit * MBIT_TO_KIB)
    if winassoc.available() and not winassoc.is_registered():
        ans = QMessageBox.question(window, APP_NAME,
                                   "¿Abrir los archivos .torrent y los enlaces magnet con Corriente?\n"
                                   "(Puedes cambiarlo en Ajustes → General.)")
        if ans == QMessageBox.StandardButton.Yes:
            try:
                winassoc.register()
                settings["file_association"] = True
            except OSError as exc:
                window.error_box(f"No se pudo registrar la asociación: {exc}")
    settings["first_run_done"] = True
    settings.save()
    window.engine.apply_settings()
