"""Click-through, manually calibrated ground-cell markers; no game interaction."""
from __future__ import annotations

import ctypes as C
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget


def cell_point(cell):
    """14-column staggered ground grid, independent of screen scale."""
    if not 0 <= cell < 560:
        raise ValueError("Case hors de la grille 0–559.")
    row, column = divmod(cell, 14)
    return QPointF(column + (row % 2) / 2, row)


def calibration_transform(cells, points):
    """Fit an affine ground plane to three non-collinear cell centres."""
    a, b, c = (cell_point(cell) for cell in cells)
    u, v = b - a, c - a
    determinant = u.x() * v.y() - u.y() * v.x()
    if abs(determinant) < 0.001:
        raise ValueError("Choisissez trois cases non alignées sur la grille.")
    p, q, r = points
    s, t = q - p, r - p
    xx = (s.x() * v.y() - t.x() * u.y()) / determinant
    xy = (t.x() * u.x() - s.x() * v.x()) / determinant
    yx = (s.y() * v.y() - t.y() * u.y()) / determinant
    yy = (t.y() * u.x() - s.y() * v.x()) / determinant
    result = QTransform(xx, yx, xy, yy,
                        p.x() - xx * a.x() - xy * a.y(),
                        p.y() - yx * a.x() - yy * a.y())
    if not result.isInvertible():
        raise ValueError("Les trois points à l’écran doivent être distincts et non alignés.")
    return result


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

    def calibrate(self, cells, points, geometry):
        transform = calibration_transform(cells, points)
        self.transform = transform
        self.setGeometry(geometry)
        self.rebuild()

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


class CalibrationOverlay(QWidget):
    finished = Signal(object)

    def __init__(self, cells, screen):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.cells = cells
        self.points = []
        self.completed = False

    def paintEvent(self, event):
        painter = QPainter(self)
        # Nonzero alpha across the window ensures native macOS receives the clicks.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 35))
        painter.fillRect(20, 20, self.width() - 40, 100, QColor(0, 0, 0, 230))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("", 18, QFont.Bold))
        cell = self.cells[min(len(self.points), 2)]
        painter.drawText(35, 57, f"Repère {len(self.points) + 1}/3 : cliquez au centre au sol de la case {cell}")
        painter.drawText(35, 94, "Pas sur le feuillage de l’arbre · Échap pour annuler · aucun clic envoyé au jeu")
        painter.setPen(QPen(QColor("#ffeb00"), 4))
        for point in self.points:
            painter.drawEllipse(point, 12, 12)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.points.append(event.position())
        if len(self.points) == 3:
            self.completed = True
            self.finished.emit(tuple(self.points))
            self.close()
        else:
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, event):
        if not self.completed:
            self.finished.emit(None)
        event.accept()
