"""Accessible, non-activating harvest compass; clipboard only, no game input."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from .gathering import GatheringMemory, countdown
from .network_protocol import VERSION
from .overlay import fullscreen_spaces


class DirectionArrow(QWidget):
    """Map axes: north is up; point to the next learned passage, not the final map."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(64, 64)
        self.direction = ""

    def set_direction(self, direction):
        if direction != self.direction:
            self.direction = direction
            self.setAccessibleName(f"Direction : {direction or 'inconnue'}")
            self.setToolTip(self.accessibleName())
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#edce88"), 3))
        painter.translate(32, 32)
        angle = next((angle for name, angle in
                      (("Nord", 0), ("Est", 90), ("Sud", 180), ("Ouest", 270))
                      if self.direction.startswith(name)), None)
        if angle is not None:
            painter.rotate(angle)
            painter.setBrush(QColor("#edce88"))
            painter.drawPolygon(QPolygonF([QPointF(0, -25), QPointF(21, 3),
                                          QPointF(7, 3), QPointF(7, 24),
                                          QPointF(-7, 24), QPointF(-7, 3), QPointF(-21, 3)]))
        elif self.direction == "Ici":
            painter.drawEllipse(QPointF(0, 0), 16, 16)
            painter.setBrush(QColor("#edce88"))
            painter.drawEllipse(QPointF(0, 0), 4, 4)
        else:
            painter.drawLine(-12, 0, 12, 0)


class CompassPanel(QWidget):
    def __init__(self, owner):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.owner = owner
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setWindowTitle("Boussole de récolte")
        self.setAccessibleName("Boussole de récolte et temps de repousse")
        self.setStyleSheet("QWidget {background:#172027;color:#ffffff;font-size:17px} QPushButton {padding:8px;border:2px solid #edce88;border-radius:5px}")
        self.setFixedWidth(390)
        layout = QVBoxLayout(self)
        self.heading = QLabel("Boussole de récolte")
        self.heading.setStyleSheet("font-size:21px;font-weight:bold;color:#edce88")
        self.heading.setToolTip("Glissez ce titre pour déplacer la boussole. La position est mémorisée pour cet écran.")
        self.heading.setCursor(Qt.SizeAllCursor)
        self.heading.installEventFilter(self)
        self.drag_offset = None
        self.placement_geometry = None
        self.placement_key = None
        self.arrow = DirectionArrow()
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.PlainText)
        self.copy = QPushButton("Copier /travel (position)")
        self.copy.setFocusPolicy(Qt.NoFocus)
        self.copy.setToolTip("Copie uniquement la position géographique. Ne lance pas de voyage ; accès aux intérieurs et instances non garanti.")
        self.copy.clicked.connect(owner.copy_destination)
        header = QHBoxLayout()
        header.addWidget(self.arrow)
        header.addWidget(self.heading, 1)
        layout.addLayout(header)
        layout.addWidget(self.details)
        layout.addWidget(self.copy)

    def bounded_position(self, position):
        geometry = self.placement_geometry
        return QPoint(max(geometry.left(), min(position.x(), geometry.right()-self.width()+1)),
                      max(geometry.top(), min(position.y(), geometry.bottom()-self.height()+1)))

    def eventFilter(self, watched, event):
        if watched is self.heading and self.placement_geometry is not None:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.MouseMove and self.drag_offset is not None:
                self.move(self.bounded_position(event.globalPosition().toPoint() - self.drag_offset))
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self.drag_offset is not None:
                self.move(self.bounded_position(event.globalPosition().toPoint() - self.drag_offset))
                self.drag_offset = None
                relative = self.pos() - self.placement_geometry.topLeft()
                self.owner.window.store.settings.compass_positions[self.placement_key] = [relative.x(), relative.y()]
                try:
                    self.owner.window.store.save()
                except OSError as exc:
                    self.owner.window.show_error(f"Position de la boussole non enregistrée : {exc}")
                return True
        return super().eventFilter(watched, event)


class Compass:
    def __init__(self, window, catalog=None):
        self.window = window
        self.memory = GatheringMemory(window.store.path.with_name(f"gathering-{VERSION}.sqlite3"), catalog)
        self.memory.select_profile(window.store.settings.compass_profile)
        self.panel = CompassPanel(self)
        self.destination = None
        self.timer = QTimer(window)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def observe(self, snapshot):
        if self.window.store.settings.compass_enabled:
            self.memory.observe(snapshot)
            self.refresh()

    def transition(self, pair):
        if self.window.store.settings.compass_enabled:
            self.memory.observe_transition(*pair)

    def clear(self):
        self.memory.reset_session()
        self.destination = None
        self.refresh()

    def refresh(self):
        w, panel = self.window, self.panel
        settings = w.store.settings
        enabled = settings.compass_enabled
        self.destination = self.memory.recommend() if enabled and w.accepting else None
        d = self.destination
        if not settings.compass_profile.strip():
            text = "Choisissez votre profil serveur / personnage dans l’onglet Boussole."
        elif not w.accepting:
            text = "Écoute arrêtée · mémoire conservée\nLes positions et repousses ne sont plus observées."
        elif d is None:
            text = "Aucune destination recommandable.\nExplorez des passages, observez les ressources et leurs repousses. Un trajet ou un délai inconnu n’est pas inventé."
        else:
            text = (f"{d.direction} · {d.name}\n[{d.x}, {d.y}] · monde {d.world_id} · {d.steps} carte(s) par passages appris\n"
                    f"{countdown(d.wait)}\n{d.certainty}")
        panel.details.setText(text)
        panel.details.setAccessibleName(text)
        panel.details.setToolTip(d.source if d else text)
        panel.arrow.set_direction(d.direction if d else "")
        panel.copy.setEnabled(bool(d and d.command))
        visible = bool(enabled and w.overlay.pid is not None and w.overlay.foreground() == w.overlay.pid)
        newly_visible = visible and not panel.isVisible()
        panel.adjustSize()
        screen = w.overlay.screen() if w.overlay.isVisible() else next(
            (s for s in QGuiApplication.screens() if f"{s.name()}:{s.geometry().width()}x{s.geometry().height()}" == settings.overlay_screen), w.screen())
        geometry = screen.availableGeometry()
        key = f"{screen.name()}:{screen.geometry().width()}x{screen.geometry().height()}"
        panel.placement_geometry, panel.placement_key = geometry, key
        saved = settings.compass_positions.get(key)
        if panel.drag_offset is None:
            position = (geometry.topLeft() + QPoint(*saved) if saved is not None
                        else QPoint(geometry.right()-panel.width()-15, geometry.top()+15))
            panel.move(panel.bounded_position(position))
        panel.setVisible(visible)
        if newly_visible:
            fullscreen_spaces(panel)

    def copy_destination(self):
        # Recompute at click time; never copy a stale recommendation after stop/profile change.
        self.refresh()
        d = self.destination
        if d and d.command:
            QApplication.clipboard().setText(d.command)
            self.panel.copy.setText("Position copiée")
            QTimer.singleShot(1500, lambda: self.panel.copy.setText("Copier /travel (position)"))


    def close(self):
        self.timer.stop()
        self.panel.close()
        self.memory.close()


class CompassSettings(QWidget):
    def __init__(self, compass):
        super().__init__(compass.window)
        self.compass = compass
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Afficher la boussole et mémoriser les observations")
        self.enabled.setChecked(compass.window.store.settings.compass_enabled)
        self.enabled.toggled.connect(self.toggle)
        layout.addWidget(self.enabled)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        settings = compass.window.store.settings
        self.profile = QComboBox()
        self.profile.setEditable(True)
        self.profile.setInsertPolicy(QComboBox.NoInsert)
        self.profile.setMinimumContentsLength(28)
        self.profile.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        settings.compass_profiles = list(dict.fromkeys(settings.compass_profiles
            + compass.memory.profiles() + ([settings.compass_profile] if settings.compass_profile else [])))
        self.profile.addItems(settings.compass_profiles)
        self.profile.setCurrentText(compass.memory.profile)
        self.profile.lineEdit().setPlaceholderText("Exemple : nom du serveur · nom du personnage")
        self.profile.setAccessibleName("Profils enregistrés · serveur et personnage")
        self.profile.activated.connect(self.use_profile)
        self.profile.lineEdit().returnPressed.connect(self.use_profile)
        form.addRow("Profil serveur / personnage", self.profile)
        layout.addLayout(form)
        use = QPushButton("Utiliser ce profil")
        use.clicked.connect(self.use_profile)
        layout.addWidget(use)
        info = QLabel("Choisissez le bon profil à chaque changement de serveur ou personnage : les cartes seules n’identifient pas le serveur.\n"
                      "Disponible d’abord ; parmi les disponibles : priorité (1 = plus haute), puis distance. Sinon : repousse la plus tôt, puis priorité et distance.\n"
                      "Seuls les passages parcourus et confirmés sont appris, dans leur sens de parcours. Les repousses hors carte restent estimées.\n"
                      "La boussole reste présente au-dessus de Dofus, même sans cible, mais se masque dans les autres applications.")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("Types de ressources observés et priorités de suivi")
        self.table.setHorizontalHeaderLabels(["Suivre", "Priorité", "Ressource", "Repousse estimée", "Origine de l’estimation"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        reload = QPushButton("Actualiser les types observés")
        reload.clicked.connect(self.load_types)
        save = QPushButton("Enregistrer les priorités")
        save.clicked.connect(self.save_priorities)
        for button in (reload, save):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.load_types()

    def save_settings(self):
        try:
            self.compass.window.store.save()
        except OSError as exc:
            self.notice.setText(f"Enregistrement impossible : {exc}")
            return False
        self.compass.refresh()
        return True

    def toggle(self, enabled):
        self.compass.window.store.settings.compass_enabled = enabled
        self.compass.memory.reset_session()
        self.save_settings()

    def use_profile(self):
        profile = self.profile.currentText().strip()
        if not profile:
            self.notice.setText("Saisissez un profil serveur / personnage pour isoler les observations.")
            return
        settings = self.compass.window.store.settings
        if profile != settings.compass_profile:
            settings.compass_profile = profile
            self.compass.memory.select_profile(profile)
        if profile not in settings.compass_profiles:
            settings.compass_profiles.append(profile)
        if self.save_settings():
            if self.profile.findText(profile, Qt.MatchExactly) == -1:
                self.profile.addItem(profile)
            self.profile.setCurrentText(profile)
            self.load_types()

    def load_types(self):
        rows = self.compass.memory.types()
        self.table.setRowCount(len(rows))
        self.controls = []
        for index, row in enumerate(rows):
            check = QCheckBox()
            check.setAccessibleName(f"Suivre {row['name']}")
            check.setChecked(bool(row["enabled"]))
            priority = QSpinBox()
            priority.setRange(1, 999)
            priority.setAccessibleName(f"Priorité de {row['name']}")
            priority.setValue(row["priority"])
            self.table.setCellWidget(index, 0, check)
            self.table.setCellWidget(index, 1, priority)
            seconds, source = self.compass.memory.estimate(row["type_id"])
            for column, text in ((2, row["name"]), (3, countdown(seconds) if seconds is not None else "Inconnue"), (4, source)):
                self.table.setItem(index, column, QTableWidgetItem(text))
            self.controls.append((row["type_id"], check, priority))
        self.table.resizeColumnsToContents()
        self.notice.setText("Les types apparaissent ici à mesure des visites. Sans durée sourcée ni repousse mesurée, aucun compte à rebours n’est inventé.")

    def save_priorities(self):
        for type_id, check, priority in self.controls:
            self.compass.memory.set_preference(type_id, check.isChecked(), priority.value())
        self.compass.refresh()
        self.notice.setText("Suivi et priorités enregistrés pour ce profil.")
