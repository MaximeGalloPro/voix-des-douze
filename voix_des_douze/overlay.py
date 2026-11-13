"""External fullscreen resource markers and keyboard adjustment; no game interaction."""
from __future__ import annotations

import ctypes as C
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from .monster_safety import (MONSTER_DISTANCE, PLAYER_HALF_WIDTH, PLAYER_ABOVE,
                             PLAYER_BELOW, cell_coordinates)


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
    # Both overlays must join another application's fullscreen Space without
    # activating our application. The adjustment panel can still become key.
    set_behavior(window, selector(b"setStyleMask:"),
                 get(window, selector(b"styleMask")) | (1 << 7))
    # macOS 13+: joining Spaces alone does not admit an overlay to another
    # application's fullscreen Space. Primary/Auxiliary are mutually exclusive
    # with CanJoinAllApplications (AppKit NSWindowCollectionBehavior).
    behavior = get(window, selector(b"collectionBehavior"))
    behavior &= ~(2 | 128 | 512 | (1 << 16) | (1 << 17))
    behavior |= 1 | 256 | (1 << 18)
    set_behavior(window, selector(b"setCollectionBehavior:"), behavior)
    order_front = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)(("objc_msgSend", objc))
    order_front(window, selector(b"orderFrontRegardless"))


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
        # Qt.Tool otherwise hides its NSPanel when the game becomes active.
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAccessibleName("Repères des ressources récoltables")
        self.resources = ()
        self.markers = ()
        self.transform = None
        self.enabled = False
        self.calibrating = False
        self.pid = None
        self.foreground = ForegroundProcess()
        self.click_target = None
        self.protection_enabled = False
        self.protection_safety = None
        self.protection_settings = None
        self.protection_target = None
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.setInterval(3000)
        self.click_timer.timeout.connect(self.clear_click_target)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh_visibility)
        self.timer.start()

    def configure(self, geometry, adjustment):
        self.clear_click_target()
        self.transform = fullscreen_transform(geometry, adjustment)
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

    def show_click_target(self, point, geometry):
        self.setGeometry(geometry)
        self.click_target = point - QPointF(geometry.topLeft())
        self.click_timer.start()
        self.update()
        self.refresh_visibility()

    def clear_click_target(self):
        self.click_timer.stop()
        self.click_target = None
        self.update()
        self.refresh_visibility()

    def show_protection_target(self, map_id, point, result):
        if self.protection_enabled:
            self.protection_target = (map_id, point - QPointF(self.geometry().topLeft()), result)
            self.update()

    def refresh_visibility(self):
        visible = bool(not self.calibrating and self.pid is not None
                       and self.foreground() == self.pid
                       and (self.click_target is not None or
                            (self.transform and (self.protection_enabled or (self.enabled and self.markers)))))
        newly_visible = visible and not self.isVisible()
        self.setVisible(visible)
        if newly_visible:
            # Qt rewrites native window properties while showing the panel.
            fullscreen_spaces(self)
        if self.protection_enabled:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        if self.protection_enabled and self.transform:
            painter.save()
            self.paint_protections(painter)
            painter.restore()
        for marker in self.markers if self.enabled else ():
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
        if self.click_target is not None:
            point = self.click_target
            # Dark outline keeps the exact centre legible on any game artwork.
            for color, width in (("#000000", 7), ("#ff4f85", 3)):
                painter.setPen(QPen(QColor(color), width))
                painter.drawLine(point + QPointF(-14, 0), point + QPointF(14, 0))
                painter.drawLine(point + QPointF(0, -14), point + QPointF(0, 14))

    def paint_protections(self, painter):
        safety = self.protection_safety
        if safety is None:
            return
        with safety.lock:
            map_id, synchronized = safety.map_id, safety.synchronized
            reserved = tuple(safety.reserved)
            groups = dict(safety.groups)
            players = dict(safety.players)
            paths = {entity: tuple(cells) for entity, cells in safety.player_paths.items()}
        font = QFont()
        font.setPointSize(11)
        painter.setFont(font)
        monster_zones, player_zones = QPainterPath(), QPainterPath()
        monster_zones.setFillRule(Qt.WindingFill)
        player_zones.setFillRule(Qt.WindingFill)
        for cell in reserved:
            u, v = cell_coordinates(cell)
            vertices = ((u - MONSTER_DISTANCE, v), (u, v - MONSTER_DISTANCE),
                        (u + MONSTER_DISTANCE, v), (u, v + MONSTER_DISTANCE))
            polygon = QPolygonF([self.transform.map(QPointF((x + y) / 2, x - y))
                                 for x, y in vertices])
            monster_zones.addPolygon(polygon)
            monster_zones.closeSubpath()
        for cells in paths.values():
            for cell in cells:
                ground = cell_point(cell)
                polygon = QPolygonF([self.transform.map(ground + QPointF(dx, dy))
                    for dx, dy in ((-PLAYER_HALF_WIDTH, -PLAYER_ABOVE),
                                   (PLAYER_HALF_WIDTH, -PLAYER_ABOVE),
                                   (PLAYER_HALF_WIDTH, PLAYER_BELOW),
                                   (-PLAYER_HALF_WIDTH, PLAYER_BELOW))])
                player_zones.addPolygon(polygon)
                player_zones.closeSubpath()
        for path, color in ((monster_zones, QColor(255, 70, 55)), (player_zones, QColor(30, 210, 255))):
            painter.setPen(QPen(color, 1))
            fill = QColor(color)
            fill.setAlpha(38)
            painter.setBrush(fill)
            painter.drawPath(path)
        for category, actors, color in (("M", groups, QColor("#ff6655")), ("J", players, QColor("#65e3ff"))):
            painter.setPen(color)
            painter.setBrush(Qt.NoBrush)
            for entity, cell in actors.items():
                point = self.transform.map(cell_point(cell))
                painter.drawEllipse(point, 4, 4)
                painter.drawText(point + QPointF(6, -6), f"{category}{entity} · case {cell}")
        if self.protection_settings is not None:
            from .map_exits import exit_segment
            settings = self.protection_settings()
            profile = settings.map_exits.get(settings.map_exit_screen)
            if (profile and settings.map_exit_screen == settings.overlay_screen
                    and profile['size'] == [self.width(), self.height()]):
                painter.setPen(QPen(QColor("#ffcf55"), 3, Qt.DashLine))
                for direction in profile['points']:
                    start, end = exit_segment(profile, direction)
                    painter.drawLine(start, end)
        global_cursor = QPointF(QCursor.pos())
        local_cursor = global_cursor - QPointF(self.geometry().topLeft())
        inverse, valid = self.transform.inverted()
        detail = "Projection invalide"
        cursor_result = "Position hors de l’écran"
        if valid and self.rect().contains(local_cursor.toPoint()):
            ground = inverse.map(local_cursor)
            x, y = ground.x() + ground.y() / 2, ground.x() - ground.y() / 2
            closest = min((abs(x - u) + abs(y - v) for u, v in
                           (cell_coordinates(cell) for cell in reserved)), default=None)
            distance = f"{closest:.2f}" if closest is not None else "aucun"
            detail = f"Curseur grille ({x:.2f}, {y:.2f}) · distance monstre : {distance}"
            if paths:
                nearest = min((cell for cells in paths.values() for cell in cells),
                              key=lambda cell: (self.transform.map(cell_point(cell)) - local_cursor).manhattanLength())
                u, v = cell_coordinates(nearest)
                detail += f" · joueur proche : dx={(x + y - u - v) / 2:.2f}, dy={x - y - u + v:.2f}"
            try:
                safety.send(map_id, (x, y), lambda: None)
                cursor_result = "AUTORISÉ par les protections de position (focus/Accessibilité non évalués)"
            except RuntimeError as exc:
                cursor_result = "REFUSÉ : " + str(exc)
        lines = [
            f"DEBUG PROTECTIONS · carte {map_id} · {'synchronisée' if synchronized else 'NON SYNCHRONISÉE — aucun clic sûr'}",
            f"Rouge : monstres d < {MONSTER_DISTANCE:g} case · cyan : joueurs |dx| ≤ {PLAYER_HALF_WIDTH:g}, dy ∈ [−{PLAYER_ABOVE:g}, {PLAYER_BELOW:g}]",
            "Contours = zones réseau mémorisées · jaune = sorties calibrées · pas les contours réels des sprites",
            detail, cursor_result,
        ]
        if self.protection_target is not None and self.protection_target[0] == map_id:
            _, point, result = self.protection_target
            painter.setPen(QPen(QColor("#ff4f85"), 3))
            painter.drawLine(point + QPointF(-12, 0), point + QPointF(12, 0))
            painter.drawLine(point + QPointF(0, -12), point + QPointF(0, 12))
            lines.append("Dernière cible (croix rose) : " + result)
        metrics = painter.fontMetrics()
        width = min(self.width() - 32, max(metrics.horizontalAdvance(line) for line in lines) + 20)
        painter.fillRect(16, 66, width, metrics.height() * len(lines) + 16, QColor(0, 0, 0, 215))
        painter.setPen(QColor("#ffffff"))
        for index, line in enumerate(lines):
            painter.drawText(26, 74 + metrics.ascent() + index * metrics.height(),
                             metrics.elidedText(line, Qt.ElideRight, width - 20))


class AdjustmentOverlay(QWidget):
    finished = Signal(object)

    def __init__(self, screen, resources, adjustment, *, debug=False, debug_directory=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setGeometry(screen.geometry())
        self.adjustment = list(adjustment)
        self.resources = tuple(r for r in resources if 0 <= r.cell < 560)
        self.completed = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.debug = None
        if debug:
            from .overlay_debug import AdjustmentDebug
            self.debug = AdjustmentDebug(self, debug_directory)

    def show(self):
        super().show()
        # Qt rewrites native properties during show; apply them afterwards.
        fullscreen_spaces(self)
        if sys.platform == "darwin" and QGuiApplication.platformName() == "cocoa":
            objc = C.CDLL("/usr/lib/libobjc.A.dylib")
            selector = objc.sel_registerName
            selector.argtypes = [C.c_char_p]
            selector.restype = C.c_void_p
            message = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_void_p)(("objc_msgSend", objc))
            window = message(int(self.winId()), selector(b"window"))
            send = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)(("objc_msgSend", objc))
            send(window, selector(b"makeKeyWindow"))
        if self.debug is not None:
            self.debug.start()

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
        painter.fillRect(15, 15, self.width() - 30, 210 if self.debug else 110, QColor(0, 0, 0, 235))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(30, 45, "Flèches : déplacer · Maj + flèches : déplacement rapide · + / − : échelle")
        painter.drawText(30, 77, "Entrée : mémoriser · Échap : annuler · touches et clics non transmis au jeu")
        x, y, scale = self.adjustment
        painter.drawText(30, 109, f"Décalage : {x:g}, {y:g} px · Échelle : {scale:.0%}")
        if self.debug is not None:
            painter.setFont(QFont("", 12))
            for index, line in enumerate(self.debug.lines()):
                painter.drawText(30, 140 + index * 28, line)

    def keyPressEvent(self, event):
        if self.debug is not None:
            self.debug.qt_key(event)
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
        if self.debug is not None and key in (*moves, Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus):
            self.debug.record("appliqué", réglage=list(self.adjustment))
        self.update()
        event.accept()

    def closeEvent(self, event):
        if self.debug is not None:
            self.debug.stop()
        if not self.completed:
            self.finished.emit(None)
        event.accept()
