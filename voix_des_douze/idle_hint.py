"""Draggable idle indicator, visible only while idle and Dofus are active."""
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel

from .overlay import fullscreen_spaces, screen_key


class IdleHint(QLabel):
    def __init__(self, window):
        super().__init__("IDLE", window, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.window = window
        self.active = False
        self.drag_offset = None
        self.placement_geometry = None
        self.placement_key = None
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAccessibleName("Mode idle actif")
        self.setToolTip("Mode idle actif · glisser pour déplacer · raccourci idle pour désactiver")
        self.setCursor(Qt.SizeAllCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("QLabel { background: #202b27; color: #bce8bd; "
                           "border: 1px solid #65946b; border-radius: 8px; "
                           "padding: 7px 14px; font-weight: bold; font-size: 14px; }")
        self.adjustSize()
        self.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)

    def set_active(self, active):
        self.active = active
        if active:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
            self.drag_offset = None
            self.hide()

    def bounded_position(self, position):
        g = self.placement_geometry
        return QPoint(max(g.left(), min(position.x(), g.right() - self.width() + 1)),
                      max(g.top(), min(position.y(), g.bottom() - self.height() + 1)))

    def refresh(self):
        w = self.window
        if not self.active or w.overlay.pid is None or w.overlay.foreground() != w.overlay.pid:
            self.hide()
            return
        settings = w.store.settings
        screen = next((s for s in QGuiApplication.screens()
                       if screen_key(s) == settings.overlay_screen), w.screen())
        self.placement_geometry = screen.geometry()
        self.placement_key = screen_key(screen)
        if self.drag_offset is None:
            saved = settings.idle_hint_positions.get(self.placement_key)
            position = (self.placement_geometry.topLeft() + QPoint(*saved) if saved is not None
                        else self.placement_geometry.topLeft() + QPoint(20, 60))
            self.move(self.bounded_position(position))
        newly_visible = not self.isVisible()
        self.show()
        if newly_visible:
            fullscreen_spaces(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None:
            self.move(self.bounded_position(event.globalPosition().toPoint() - self.drag_offset))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_offset is not None:
            self.move(self.bounded_position(event.globalPosition().toPoint() - self.drag_offset))
            self.drag_offset = None
            relative = self.pos() - self.placement_geometry.topLeft()
            self.window.store.settings.idle_hint_positions[self.placement_key] = [relative.x(), relative.y()]
            try:
                self.window.store.save()
            except OSError as exc:
                self.window.show_error(f"Position du badge IDLE non enregistrée : {exc}")
            event.accept()
        else:
            super().mouseReleaseEvent(event)
