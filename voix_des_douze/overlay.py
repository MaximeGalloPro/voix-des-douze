"""External fullscreen resource markers and keyboard adjustment; no game interaction."""
from __future__ import annotations

import ctypes as C
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget


def cell_point(cell):
    """14-column staggered ground grid, independent of screen scale."""
    if not 0 <= cell < 560:
        raise ValueError("Case hors de la grille 0–559.")
    row, column = divmod(cell, 14)
    return QPointF(column + (row % 2) / 2, row)


def fullscreen_transform(geometry, adjustment):
    """Centre the staggered grid; uniform scaling keeps the ground diamonds intact."""
    x, y, scale = adjustment
    step = min(geometry.width() / 14, geometry.height() / 10) * 0.9 * scale
    return QTransform(step, 0, 0, step / 4,
                      geometry.width() / 2 + x - 6.75 * step,
                      geometry.height() / 2 + y - 19.5 * step / 4)


def screen_key(screen):
    size = screen.geometry().size()
    return f"{screen.name()}:{size.width()}x{size.height()}"


def fullscreen_spaces(widget):
    """Allow external overlays in native macOS fullscreen Spaces."""
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        return
    objc = C.CDLL("/usr/lib/libobjc.A.dylib")
    selector = objc.sel_registerName
    selector.argtypes = [C.c_char_p]
    selector.restype = C.c_void_p
    message = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(("objc_msgSend", objc))
    window = message(int(widget.winId()), selector(b"window"))
    get = C.CFUNCTYPE(C.c_ulong, C.c_void_p, C.c_void_p)(("objc_msgSend", objc))
    set_behavior = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_ulong)(("objc_msgSend", objc))
    set_behavior(window, selector(b"setCollectionBehavior:"),
                 (get(window, selector(b"collectionBehavior")) & ~(2 | 128)) | 1 | 256)


class ProcessSerialNumber(C.Structure):
    _fields_ = [("high", C.c_uint32), ("low", C.c_uint32)]


class ForegroundProcess:
    """Carbon lookup only: no keyboard monitoring or Accessibility permission."""
    def __init__(self):
        self.lib = None
        if sys.platform == "darwin":
            self.lib = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            self.lib.GetFrontProcess.argtypes = [C.POINTER(ProcessSerialNumber)]
            self.lib.GetFrontProcess.restype = C.c_int32
            self.lib.GetProcessPID.argtypes = [C.POINTER(ProcessSerialNumber), C.POINTER(C.c_int32)]
            self.lib.GetProcessPID.restype = C.c_int32

    def __call__(self):
        if self.lib is None:
            return None
        serial, pid = ProcessSerialNumber(), C.c_int32()
        if self.lib.GetFrontProcess(C.byref(serial)) or self.lib.GetProcessPID(C.byref(serial), C.byref(pid)):
            return None
        return pid.value


@dataclass(frozen=True)
class Marker:
    point: QPointF
    label: str


class ResourceOverlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAccessibleName("Repères des ressources récoltables")
        self.resources = ()
        self.markers = ()
        self.transform = None
        self.enabled = False
        self.calibrating = False
        self.pid = None
        self.foreground = ForegroundProcess()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh_visibility)
        self.timer.start()

    def configure(self, geometry, adjustment):
        self.transform = fullscreen_transform(geometry, adjustment)
        self.setGeometry(geometry)
        self.rebuild()

    def showEvent(self, event):
        fullscreen_spaces(self)
        super().showEvent(event)

    def set_resources(self, resources):
        self.resources = resources
        self.rebuild()

    def rebuild(self):
        self.markers = tuple(Marker(self.transform.map(cell_point(resource.cell)), resource.name)
                             for resource in self.resources
                             if resource.harvestable and 0 <= resource.cell < 560) if self.transform else ()
        self.update()
        self.refresh_visibility()

    def refresh_visibility(self):
        visible = bool(self.enabled and not self.calibrating and self.transform and self.markers and self.pid is not None
                       and self.foreground() == self.pid)
        self.setVisible(visible)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        for marker in self.markers:
            point = marker.point
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#000000"), 10))
            painter.drawEllipse(point, 19, 19)
            painter.setPen(QPen(QColor("#ffeb00"), 5))
            painter.drawEllipse(point, 19, 19)
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(marker.label) + 16
            x = max(0, min(point.x() - width / 2, self.width() - width))
            y = max(0, min(point.y() - 60, self.height() - metrics.height() - 8))
            painter.fillRect(int(x), int(y), width, metrics.height() + 8, QColor(0, 0, 0, 230))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QPointF(x + 8, y + metrics.ascent() + 4), marker.label)


class AdjustmentOverlay(QWidget):
    finished = Signal(object)

    def __init__(self, screen, resources, adjustment):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(screen.geometry())
        self.adjustment = list(adjustment)
        self.resources = tuple(r for r in resources if r.harvestable and 0 <= r.cell < 560)
        self.completed = False
        self.setFocusPolicy(Qt.StrongFocus)

    def showEvent(self, event):
        fullscreen_spaces(self)
        super().showEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 20))
        transform = fullscreen_transform(self.geometry(), self.adjustment)
        painter.setPen(QPen(QColor(255, 235, 0, 90), 1))
        # A grid remains adjustable even before receiving any resource positions.
        for cell in range(560):
            point = transform.map(cell_point(cell))
            painter.drawEllipse(point, 2, 2)
        painter.setFont(QFont("", 16, QFont.Bold))
        for resource in self.resources:
            point = transform.map(cell_point(resource.cell))
            painter.setPen(QPen(QColor("#000000"), 9))
            painter.drawEllipse(point, 19, 19)
            painter.setPen(QPen(QColor("#ffeb00"), 4))
            painter.drawEllipse(point, 19, 19)
            painter.drawText(point + QPointF(24, 0), resource.name)
        painter.fillRect(15, 15, self.width() - 30, 110, QColor(0, 0, 0, 235))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(30, 45, "Flèches : déplacer · Maj + flèches : déplacement rapide · + / − : échelle")
        painter.drawText(30, 77, "Entrée : mémoriser · Échap : annuler · touches et clics non transmis au jeu")
        x, y, scale = self.adjustment
        painter.drawText(30, 109, f"Décalage : {x:g}, {y:g} px · Échelle : {scale:.0%}")

    def keyPressEvent(self, event):
        key = event.key()
        step = 10 if event.modifiers() & Qt.ShiftModifier else 1
        moves = {Qt.Key_Left: (-step, 0), Qt.Key_Right: (step, 0),
                 Qt.Key_Up: (0, -step), Qt.Key_Down: (0, step)}
        if key in moves:
            dx, dy = moves[key]
            self.adjustment[0] += dx
            self.adjustment[1] += dy
        elif key in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
            change = -0.01 if key == Qt.Key_Minus else 0.01
            self.adjustment[2] = round(max(0.2, min(3.0, self.adjustment[2] + change)), 2)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.completed = True
            self.finished.emit(tuple(self.adjustment))
            self.close()
        elif key == Qt.Key_Escape:
            self.close()
        self.update()
        event.accept()

    def closeEvent(self, event):
        if not self.completed:
            self.finished.emit(None)
        event.accept()
