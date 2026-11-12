"""Read-only, profile-scoped map of observed resources; no game interaction."""
from collections import Counter
from html import escape

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QGraphicsScene, QGraphicsView,
                              QHBoxLayout, QLabel, QPushButton, QVBoxLayout)


CELL = 64


class CompassMap(QDialog):
    def __init__(self, compass):
        super().__init__(compass.window)
        self.compass = compass
        self.setWindowTitle("Carte des ressources")
        self.resize(780, 580)
        self.signature = None
        self.tiles = {}
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.profile = QLabel()
        controls.addWidget(self.profile)
        controls.addStretch()
        self.world = QComboBox()
        self.world.setAccessibleName("Monde affiché")
        self.world.currentIndexChanged.connect(self.draw_map)
        controls.addWidget(self.world)
        for text, callback in (("−", lambda: self.zoom(1 / 1.25)),
                               ("+", lambda: self.zoom(1.25)),
                               ("Tout voir", self.fit_map)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        help_button = QPushButton("(?)")
        help_button.setAccessibleName("Aide de la carte des ressources")
        help_button.setToolTip("Cases explorées uniquement. Chiffre : total des ressources mémorisées, pas des ressources garanties disponibles.\n"
                               "Bleu : carte courante. Survol : coordonnées, ressources et dernier état observé.\n"
                               "Hors de la carte courante, les observations peuvent avoir changé.\n"
                               "Glisser pour déplacer la vue ; + et − pour zoomer. Les mondes sont séparés.")
        controls.addWidget(help_button)
        layout.addLayout(controls)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setAccessibleName("Grille des cartes explorées et ressources mémorisées")
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setBackgroundBrush(QColor("#18212b"))
        layout.addWidget(self.view)
        self.empty = QLabel()
        layout.addWidget(self.empty)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        if not self.isVisible():
            return
        memory = self.compass.memory
        signature = (memory.profile, memory.revision, memory.current)
        if signature == self.signature:
            return
        profile_changed = self.signature is None or self.signature[0] != memory.profile
        self.signature = signature
        self.profile.setText(memory.profile or "Aucun profil")
        self.profile.setTextFormat(Qt.PlainText)
        resources = {}
        for row in memory.db.execute("SELECT * FROM resources WHERE profile=? ORDER BY map_id,name,element_id",
                                     (memory.profile,)):
            resources.setdefault(row["map_id"], []).append(row)
        known = set(resources)
        known.update(row[0] for row in memory.db.execute(
            "SELECT map_id FROM visited_maps WHERE profile=?", (memory.profile,)))
        for row in memory.db.execute("SELECT origin,target FROM edges WHERE profile=?", (memory.profile,)):
            known.update(row)
        if memory.current is not None:
            known.add(memory.current)
        self.tiles = {}
        missing = 0
        for map_id in sorted(known):
            coordinate = memory.catalog["maps"].get(str(map_id))
            if not coordinate or coordinate["world_id"] < 0:
                missing += 1
                continue
            key = (coordinate["world_id"], coordinate["x"], coordinate["y"])
            self.tiles.setdefault(key, []).append((map_id, resources.get(map_id, [])))
        worlds = sorted({key[0] for key in self.tiles})
        selected = None if profile_changed else self.world.currentData()
        current = memory.catalog["maps"].get(str(memory.current))
        if selected not in worlds:
            selected = current["world_id"] if current and current["world_id"] in worlds else (worlds[0] if worlds else None)
        self.world.blockSignals(True)
        self.world.clear()
        for world in worlds:
            self.world.addItem(f"Monde {world}", world)
        self.world.setCurrentIndex(self.world.findData(selected))
        self.world.blockSignals(False)
        self.empty.setText(f"{missing} carte(s) sans coordonnées connues." if missing else "")
        self.draw_map(fit=profile_changed)

    def draw_map(self, index=None, *, fit=True):
        was_empty = not self.scene.items()
        self.scene.clear()
        memory = self.compass.memory
        world = self.world.currentData()
        current = memory.current
        for (world_id, x, y), maps in self.tiles.items():
            if world_id != world:
                continue
            count = sum(len(rows) for _, rows in maps)
            here = any(map_id == current for map_id, _ in maps)
            details = [f"<b>[{x}, {y}] · {count} ressource(s) mémorisée(s)</b>"]
            for map_id, rows in maps:
                status = "carte courante" if map_id == current else "dernière observation · à vérifier"
                details.append(f"<br><b>Carte {map_id} — {status}</b>")
                counts = Counter((row["name"], self.state_label(row)) for row in rows)
                for (name, state), quantity in sorted(counts.items()):
                    details.append(f"{escape(name)} × {quantity} — {state}")
                if not rows:
                    details.append("Aucune ressource mémorisée.")
            tooltip = "<qt>" + "<br>".join(details) + "</qt>"
            tile = self.scene.addRect(x * CELL, y * CELL, CELL, CELL,
                                      QPen(QColor("#90caf9" if here else "#566779")),
                                      QColor("#235777" if here else "#293a48"))
            tile.setToolTip(tooltip)
            number = self.scene.addSimpleText(str(count) if count else "·", QFont("", 16))
            number.setBrush(QColor("#ffffff"))
            number.setPos(x * CELL + (CELL - number.boundingRect().width()) / 2, y * CELL + 6)
            number.setToolTip(tooltip)
            coordinates = self.scene.addSimpleText(f"{x},{y}", QFont("", 8))
            coordinates.setBrush(QColor("#c1cbd6"))
            coordinates.setPos(x * CELL + (CELL - coordinates.boundingRect().width()) / 2, y * CELL + 43)
            coordinates.setToolTip(tooltip)
        bounds = self.scene.itemsBoundingRect()
        self.scene.setSceneRect(bounds.adjusted(-CELL, -CELL, CELL, CELL))
        if not self.scene.items():
            self.empty.setText("Aucune carte observée pour ce profil." if not self.tiles else self.empty.text())
        if fit or was_empty:
            self.fit_map()

    @staticmethod
    def state_label(row):
        if row["state"] == 0:
            return "mature · action disponible" if row["enabled"] else "mature · action indisponible"
        return {1: "non mature", 2: "récolte en cours"}.get(row["state"], "état inconnu")

    def fit_map(self):
        if self.scene.items():
            self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
            # Keep small explored areas readable rather than scaling one cell to the window.
            if self.view.transform().m11() > 1.5:
                self.view.resetTransform()
                self.view.scale(1.5, 1.5)

    def zoom(self, factor):
        scale = self.view.transform().m11() * factor
        if 0.05 <= scale <= 4:
            self.view.scale(factor, factor)
