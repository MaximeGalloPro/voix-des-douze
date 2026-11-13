"""Accessible, non-activating harvest compass; clipboard only, no game input."""
from __future__ import annotations

import sqlite3
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
    QHBoxLayout, QLabel, QLayout, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from .gathering import GatheringMemory, countdown
from .network_protocol import VERSION
from .overlay import fullscreen_spaces
from .compass_debug import CompassJournal
from .travel import TravelCalibration
from .compass_map import CompassMap


class DirectionArrow(QWidget):
    """Map axes: north is up; point to the next learned passage, not the final map."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(84, 84)
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
        painter.translate(42, 42)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#52616b"), 1))
        painter.drawEllipse(QPointF(0, 0), 38, 38)
        for angle in range(0, 360, 30):
            painter.save()
            painter.rotate(angle)
            painter.setPen(QPen(QColor("#edce88" if angle % 90 == 0 else "#52616b"), 2))
            painter.drawLine(0, -34, 0, -29 if angle % 90 == 0 else -32)
            painter.restore()
        painter.setPen(QPen(QColor("#edce88"), 2))
        angle = next((angle for name, angle in
                      (("Nord", 0), ("Est", 90), ("Sud", 180), ("Ouest", 270))
                      if self.direction.startswith(name)), None)
        if angle is not None:
            painter.rotate(angle)
            painter.setBrush(QColor("#edce88"))
            painter.drawPolygon(QPolygonF([QPointF(0, -24), QPointF(14, 14),
                                          QPointF(0, 7), QPointF(-14, 14)]))
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
        self.setObjectName("compass_panel")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("""
            QWidget { color:#ffffff; font-size:16px; background:transparent; }
            QLabel { font-weight:600; }
            QPushButton {
                background:transparent; color:#f4dfad; font-size:16px; font-weight:600;
                padding:4px; border:1px solid #9c8961; border-radius:6px;
            }
            QPushButton:hover { background:rgba(23,35,44,160); border-color:#edce88; }
            QPushButton:disabled { color:#a8b1b5; border-color:#52616b; }
        """)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetFixedSize)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self.drag_offset = None
        self.placement_geometry = None
        self.placement_key = None
        self.move_handle = QPushButton("✥", self)
        self.move_handle.setFixedSize(26, 26)
        self.move_handle.setFocusPolicy(Qt.NoFocus)
        self.move_handle.setAccessibleName("Déplacer la boussole")
        self.move_handle.setToolTip("Maintenir et glisser pour déplacer la boussole.")
        self.move_handle.setCursor(Qt.SizeAllCursor)
        self.move_handle.installEventFilter(self)
        self.move_handle.hide()
        self.arrow = DirectionArrow()
        self.distance = QLabel("—")
        self.distance.setStyleSheet("font-size:28px;color:#edce88")
        self.distance.setToolTip("Distance en cartes par les passages appris.")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(self.arrow)
        header.addWidget(self.distance)
        layout.addLayout(header)
        self.resource = QLabel()
        self.resource.setWordWrap(True)
        self.resource.setTextFormat(Qt.PlainText)
        self.resource.setAlignment(Qt.AlignCenter)
        self.resource.setFixedWidth(self.arrow.width())
        self.wait = QLabel()
        self.wait.setTextFormat(Qt.PlainText)
        self.wait.setAlignment(Qt.AlignCenter)
        self.wait.setFixedWidth(self.arrow.width())
        self.wait.setStyleSheet("color:#edce88")
        self.copy = QPushButton("—")
        self.copy.setFocusPolicy(Qt.NoFocus)
        self.copy.setFixedWidth(self.arrow.width())
        self.copy.setToolTip("Copier /travel : position géographique seulement, sans lancer de voyage ; accès aux intérieurs et instances non garanti.")
        self.copy.clicked.connect(owner.copy_destination)
        layout.addWidget(self.resource, 0, Qt.AlignLeft)
        layout.addWidget(self.wait, 0, Qt.AlignLeft)
        layout.addWidget(self.copy, 0, Qt.AlignLeft)
        self.move_handle.move(4, 4)
        self.move_handle.raise_()
        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(80)
        self.hover_timer.timeout.connect(self.refresh_hover)

    def refresh_hover(self):
        # Cocoa may pass transparent pixels through without enter/leave events.
        hovered = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        self.move_handle.setVisible(self.isVisible() and (hovered or self.drag_offset is not None))

    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()
        self.refresh_hover()

    def hideEvent(self, event):
        self.hover_timer.stop()
        self.move_handle.hide()
        super().hideEvent(event)

    def enterEvent(self, event):
        self.move_handle.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.drag_offset is None:
            self.move_handle.hide()
        super().leaveEvent(event)

    def bounded_position(self, position):
        geometry = self.placement_geometry
        return QPoint(max(geometry.left(), min(position.x(), geometry.right()-self.width()+1)),
                      max(geometry.top(), min(position.y(), geometry.bottom()-self.height()+1)))

    def eventFilter(self, watched, event):
        if watched is self.move_handle and self.placement_geometry is not None:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.MouseMove and self.drag_offset is not None:
                self.move(self.bounded_position(event.globalPosition().toPoint() - self.drag_offset))
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self.drag_offset is not None:
                self.move(self.bounded_position(event.globalPosition().toPoint() - self.drag_offset))
                self.drag_offset = None
                self.refresh_hover()
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
        self.journal = CompassJournal(window.store.path.parent / "debug", window.show_error)
        self.calibration = TravelCalibration()
        self.calibration_view = None
        self.map_dialog = None
        self.timer = QTimer(window)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def observe(self, snapshot):
        click = getattr(self.window, "resource_click", None)
        idle = click is not None and click.idle
        if self.window.store.settings.compass_enabled or idle:
            self.journal.arrival(self.memory.profile, snapshot, self.memory.clock())
            measured = self.memory.observe(snapshot)
            if idle:
                if not self.memory.profile:
                    self.window.idle_measurement.setText("Mesure : choisissez un profil serveur / personnage dans Boussole.")
                for resource, seconds in measured:
                    median, source = self.memory.estimate(resource.type_id)
                    self.window.idle_measurement.setText(
                        f"Repousse observée : {resource.name} · élément {resource.element_id} · "
                        f"{seconds:.1f} s · médiane {median:.1f} s · {source}")
            self.refresh()
        else:
            self.memory.reset_session()

    def transition(self, pair):
        if self.window.store.settings.compass_enabled:
            self.memory.observe_transition(*pair)

    def timing(self, event):
        if not self.window.store.settings.compass_enabled or not self.window.accepting:
            return
        self.journal.sync_profile(self.memory.profile)
        if event["origin"] != event["target"]:
            requested = event["requested_at"]
            self.journal.record("changement de carte", self.memory.clock(), **event,
                                délai_demande_arrivée_s=event["arrived_at"]-requested
                                if requested is not None and event["requested"] == event["target"] else None)
        sample = self.calibration.observe(event, self.memory.catalog["maps"])
        if sample is not None:
            self.journal.record("échantillon de trajet", self.memory.clock(),
                                secondes=sample, nombre=len(self.calibration.samples))
        if len(self.calibration.samples) == 40 and self.calibration.active:
            try:
                self.finish_calibration()
            except sqlite3.Error as exc:
                self.window.show_error(f"Calibration non enregistrée : {exc}")
        self.refresh()

    def finish_calibration(self):
        seconds = self.calibration.finish()
        self.memory.save_travel_seconds(seconds)
        self.journal.record("calibration terminée", self.memory.clock(),
                            secondes_par_carte=seconds, mesures=self.calibration.samples)

    def cancel_calibration(self):
        if self.calibration.active:
            self.journal.record("calibration annulée", self.memory.clock())
        self.calibration.cancel()

    def clear(self):
        self.cancel_calibration()
        self.memory.reset_session()
        self.journal.reset(self.memory.profile)
        self.destination = None
        self.refresh()

    def refresh(self):
        w, panel = self.window, self.panel
        settings = w.store.settings
        enabled = settings.compass_enabled
        if self.calibration.active and (not enabled or not w.accepting
                                        or self.calibration.profile != self.memory.profile):
            self.cancel_calibration()
        if self.calibration_view is not None:
            self.calibration_view.update_calibration()
        self.destination = self.memory.recommend() if enabled and w.accepting else None
        d = self.destination
        if enabled and w.accepting:
            self.journal.prediction(self.memory, d)
        else:
            self.journal.reset(self.memory.profile)
        if not settings.compass_profile.strip():
            text = "Choisissez votre profil serveur / personnage dans l’onglet Boussole."
        elif not w.accepting:
            text = "Écoute arrêtée · mémoire conservée\nLes positions et repousses ne sont plus observées."
        elif d is None:
            text = "Aucune destination recommandable.\nExplorez des passages, observez les ressources et leurs repousses. Un trajet ou un délai inconnu n’est pas inventé."
        else:
            text = (f"{d.direction} · [{d.x}, {d.y}] · monde {d.world_id}\n"
                    f"{d.steps} carte(s) par passages appris · {d.resource_count} ressource(s) de même priorité disponibles\n"
                    + (f"Trajet estimé : {d.travel_seconds:.1f} s\n" if d.travel_seconds is not None else "Durée du trajet non calibrée\n")
                    + d.certainty)
        panel.arrow.set_direction(d.direction if d else "")
        panel.setAccessibleDescription(text)
        panel.arrow.setToolTip(text + (f"\n{d.source}" if d else ""))
        panel.resource.setText(d.name if d else "—")
        seconds = max(0, int(d.wait + 0.999)) if d else 0
        panel.wait.setText(f"{seconds // 60}:{seconds % 60:02d}" if seconds else "")
        panel.wait.setVisible(bool(d and seconds))
        panel.wait.setToolTip(f"{d.certainty}\n{d.source}" if d else text)
        panel.distance.setText(str(d.steps) if d else "—")
        panel.distance.setAccessibleName(f"Distance : {d.steps} cartes" if d else "Aucune destination")
        panel.copy.setText(f"[{d.x}, {d.y}]" if d else "—")
        panel.copy.setAccessibleName(f"Copier /travel {d.x},{d.y}" if d and d.command else "Position non copiable")
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
            self.panel.copy.setToolTip(f"Copié : {d.command}")

    def show_map(self):
        if self.map_dialog is None:
            self.map_dialog = CompassMap(self)
        self.map_dialog.show()
        self.map_dialog.raise_()
        self.map_dialog.activateWindow()


    def close(self):
        self.timer.stop()
        self.cancel_calibration()
        self.journal.close()
        self.panel.close()
        if self.map_dialog is not None:
            self.map_dialog.close()
        self.memory.close()


class CompassSettings(QWidget):
    def __init__(self, compass):
        super().__init__(compass.window)
        self.compass = compass
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Afficher la boussole et mémoriser les observations")
        self.enabled.setChecked(compass.window.store.settings.compass_enabled)
        self.enabled.toggled.connect(self.toggle)
        heading = QHBoxLayout()
        heading.addWidget(self.enabled)
        self.map_button = QPushButton("Voir la carte")
        self.map_button.clicked.connect(compass.show_map)
        heading.addWidget(self.map_button)
        heading.addStretch()
        self.help = QPushButton("(?)")
        self.help.setFixedWidth(38)
        self.help.setAccessibleName("Aide boussole et calibration")
        heading.addWidget(self.help)
        layout.addLayout(heading)
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
        help_text = ("Choisissez le bon profil à chaque changement de serveur ou personnage.\n"
                     "Disponible d’abord, puis priorité (10 par défaut, 1 = plus haute), puis trajet / quantité disponible de même priorité.\n"
                     "Sans ressource prête : prochaine échéance, priorité, distance. Les repousses hors carte restent estimées.\n"
                     "Seuls les passages parcourus et confirmés sont appris, dans leur sens de parcours.\n"
                     "Les types de ressources apparaissent après observation. Sans durée connue, aucun délai n’est inventé.")
        self.calibration_status = QLabel()
        self.calibration_status.setWordWrap(True)
        layout.addWidget(self.calibration_status)
        self.help.setToolTip(help_text + "\n\nCalibration : premier passage d’amorçage, puis 20 à 40 passages sans récolter ni s’arrêter.\n"
                             "Pause avant tout arrêt ; Reprendre amorce une nouvelle séquence sans compter l’immobilité.\n"
                             "Terminer enregistre la médiane à partir de 20 mesures ; à 40, enregistrement automatique.\n"
                             "Hors calibration, aucune durée passée sur une carte ne modifie la mesure.")
        calibration_controls = QHBoxLayout()
        self.calibrate = QPushButton("Démarrer la calibration")
        self.calibrate.clicked.connect(self.toggle_calibration)
        self.pause_calibration = QPushButton("Pause")
        self.pause_calibration.clicked.connect(self.toggle_calibration_pause)
        self.cancel_calibration = QPushButton("Annuler la calibration")
        self.cancel_calibration.clicked.connect(self.abort_calibration)
        for button in (self.calibrate, self.pause_calibration, self.cancel_calibration):
            calibration_controls.addWidget(button)
        layout.addLayout(calibration_controls)
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
        compass.calibration_view = self
        self.update_calibration()

    def update_calibration(self):
        c = self.compass.calibration
        seconds = self.compass.memory.seconds_per_map
        baseline = f"{seconds:.1f} s / carte enregistrées." if seconds is not None else "Durée par carte non calibrée."
        self.calibration_status.setText(
            f"{baseline} Calibration {'en pause' if c.paused else 'en cours'} : {len(c.samples)} / 40 passages (20 minimum)."
            if c.active else baseline)
        self.calibrate.setText("Terminer la calibration" if c.active else "Démarrer la calibration")
        self.pause_calibration.setText("Reprendre" if c.paused else "Pause")
        self.pause_calibration.setEnabled(c.active)
        self.cancel_calibration.setEnabled(c.active)

    def toggle_calibration(self):
        compass = self.compass
        try:
            if compass.calibration.active:
                compass.finish_calibration()
            elif (compass.window.accepting and compass.window.store.settings.compass_enabled
                  and compass.memory.profile):
                compass.calibration.start(compass.memory.profile)
                compass.journal.sync_profile(compass.memory.profile)
                compass.journal.record("calibration démarrée", compass.memory.clock())
            else:
                self.notice.setText("Activez la boussole, choisissez un profil et démarrez l’écoute avant la calibration.")
                return
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.notice.setText(str(exc))
        self.update_calibration()
        compass.refresh()

    def toggle_calibration_pause(self):
        self.compass.calibration.pause()
        self.compass.journal.record("calibration pause/reprise", self.compass.memory.clock(),
                                   pause=self.compass.calibration.paused)
        self.update_calibration()

    def abort_calibration(self):
        self.compass.cancel_calibration()
        self.update_calibration()

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
            self.compass.cancel_calibration()
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
        self.notice.clear()

    def save_priorities(self):
        for type_id, check, priority in self.controls:
            self.compass.memory.set_preference(type_id, check.isChecked(), priority.value())
        self.compass.refresh()
        self.notice.setText("Suivi et priorités enregistrés pour ce profil.")
