"""Screen-local map exit calibration; calibration never sends input to Dofus."""
import random

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .overlay import AdjustmentOverlay

DIRECTIONS = {"left": "Gauche", "right": "Droite", "up": "Haut", "down": "Bas"}


def exit_segment(profile, direction):
    x, y = profile["points"][direction]
    width, height = profile["size"]
    rx, ry = profile["radius"]
    if direction in ("left", "right"):
        return QPointF(x, max(0, y - ry)), QPointF(x, min(height - 1, y + ry))
    return QPointF(max(0, x - rx), y), QPointF(min(width - 1, x + rx), y)


def sample_exit(profile, direction):
    """Return a local logical-pixel point; only the intended axis varies."""
    start, end = exit_segment(profile, direction)
    if direction in ("left", "right"):
        return QPointF(start.x(), random.uniform(start.y(), end.y()))
    return QPointF(random.uniform(start.x(), end.x()), start.y())


def exit_radius(screen):
    # Geometry and physicalSize give logical pixels/mm even on Retina displays.
    size = screen.physicalSize()
    geometry = screen.geometry()
    return [max(1, round(30 * pixels / millimetres)) if millimetres > 0
            else max(1, round(3 * screen.logicalDotsPerInch() / 2.54))
            for pixels, millimetres in ((geometry.width(), size.width()),
                                       (geometry.height(), size.height()))]


class ExitCalibration(AdjustmentOverlay):
    """Reuse the native fullscreen calibration window lifecycle and cancellation."""
    def __init__(self, screen, saved=None):
        super().__init__(screen, (), (0, 0, 1))
        self.size_profile = [screen.geometry().width(), screen.geometry().height()]
        compatible = saved is not None and saved["size"] == self.size_profile
        self.points = {key: list(value) for key, value in saved["points"].items()} if compatible else {}
        self.radius = exit_radius(screen)
        self.direction = "left"
        self.setWindowTitle("Régler les sorties de carte")
        self.bar = QWidget(self)
        self.bar.setStyleSheet(
            "QWidget {background:#17202b;color:#ffffff;border-radius:6px}"
            "QPushButton {background:#324650;padding:6px;border:1px solid #69808f}"
            "QPushButton:checked {background:#8c681d;border:2px solid #ffdd68}"
            "QPushButton:disabled {color:#89939b}")
        layout = QVBoxLayout(self.bar)
        self.instructions = QLabel(
            "Cliquez le point de sortie dans Dofus pour chaque direction. Les clics sont interceptés.\n"
            "Zone aléatoire : environ ±3 cm · verticale à gauche/droite, horizontale en haut/bas.\n"
            "Entrée : enregistrer les 4 points · Échap : annuler. Aucun clic n’est envoyé au jeu.")
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)
        row = QHBoxLayout()
        self.buttons = {}
        for direction, label in DIRECTIONS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, d=direction: self.select(d))
            row.addWidget(button)
            self.buttons[direction] = button
        layout.addLayout(row)
        self.state = QLabel()
        layout.addWidget(self.state)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Enregistrer les 4 sorties")
        self.save_button.clicked.connect(self.commit)
        actions.addWidget(self.save_button)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.close)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.bar.setGeometry(20, max(10, self.height() // 2 - 110), min(680, self.width() - 40), 220)
        self.select("left")

    def select(self, direction):
        self.direction = direction
        for key, button in self.buttons.items():
            button.setChecked(key == direction)
        self.state.setText(f"À placer : {DIRECTIONS[direction]} · {len(self.points)}/4 sorties définies")
        self.save_button.setEnabled(len(self.points) == 4)
        profile = self.profile()
        self.samples = {key: [sample_exit(profile, key) for _ in range(9)]
                        for key in self.points}
        self.update()

    def profile(self):
        return {"size": list(self.size_profile), "radius": list(self.radius),
                "points": {key: list(value) for key, value in self.points.items()}}

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        point = event.position()
        if not self.rect().contains(point.toPoint()):
            return
        self.points[self.direction] = [max(0, min(self.width() - 1, round(point.x()))),
                                       max(0, min(self.height() - 1, round(point.y())))]
        following = next((key for key in DIRECTIONS if key not in self.points), self.direction)
        self.select(following)
        event.accept()

    def commit(self):
        if len(self.points) != 4:
            return
        self.completed = True
        self.finished.emit(self.profile())
        self.close()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commit()
        elif event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self.select({Qt.Key_Left: "left", Qt.Key_Right: "right",
                         Qt.Key_Up: "up", Qt.Key_Down: "down"}[event.key()])
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 35))
        painter.setFont(QFont("", 13, QFont.Bold))
        profile = self.profile()
        for direction, (x, y) in self.points.items():
            color = QColor("#ffdd68" if direction == self.direction else "#75e2bf")
            start, end = exit_segment(profile, direction)
            painter.setPen(QPen(color, 5))
            painter.drawLine(start, end)
            painter.setPen(QPen(QColor("#10252b"), 4))
            for sample in self.samples[direction]:
                painter.drawPoint(sample)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            point = QPointF(x, y)
            painter.drawEllipse(point, 7, 7)
            painter.drawLine(point + QPointF(-12, 0), point + QPointF(12, 0))
            painter.drawLine(point + QPointF(0, -12), point + QPointF(0, 12))
            # Keep labels readable even when a point is set at a screen edge.
            label_x = min(max(12, x + 18), max(12, self.width() - 150))
            label_y = min(max(25, y - 18), self.height() - 15)
            painter.drawText(QPointF(label_x, label_y), DIRECTIONS[direction] + " · ±3 cm")
