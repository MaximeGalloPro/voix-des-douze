from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollArea, QSlider, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .hotkeys import GlobalHotkeys, KEYS
from .network import NetworkSession, clients
from .playback import Playback, Utterance
from .settings import Store, model_directory
from .engines import ENGINES, ManagedSpeechEngine, available_voices
from .alerts import ResourceAlert
from .overlay import AdjustmentOverlay, ResourceOverlay, screen_key
from .resource_click import FirstResourceClick
from .compass import Compass, CompassSettings
from .map_exits import ExitCalibration
from .idle_route import RouteEditor


class Events(QObject):
    clients = Signal(object)
    error = Signal(str)
    network = Signal(int, str, object)
    prepared = Signal(bool, str)
    audio = Signal(int, int, float)
    hotkey = Signal(str)
    ready = Signal(int)
    engine_prepared = Signal(int, bool, str)
    preparation_progress = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, store: Store | None = None, models: Path | None = None):
        super().__init__()
        self.setWindowTitle("Voix des Douze · lecteur local")
        self.resize(960, 900)
        self.store = store or Store()
        self.events = Events()
        self.models = models or model_directory()
        self.engine_id = self.store.settings.engine
        self.tts_generation = 0
        self.tts_ready = False
        self.preparation_process = None
        self.preparation_lock = threading.Lock()
        self.preparing_engine = False
        self.playback = None
        self.voices = []
        self.voice_map = {}
        self.listener = None
        self.generation = 0
        self.accepting = False
        self.paused = False
        self.last_dialogue = None
        self.resource_alert = ResourceAlert(self, self.show_error)
        self.overlay = ResourceOverlay()
        self.adjustment = None
        self.exit_calibration = None
        self.resource_click = FirstResourceClick(self)
        self.shutting_down = False
        self.build_ui()
        self.resource_click.idle_changed.connect(self.idle_state_changed)
        self.compass = Compass(self)
        self.compass_settings = CompassSettings(self.compass)
        self.compass_layout.addWidget(self.compass_settings)
        self.events.ready.connect(self.engine_ready)
        self.events.engine_prepared.connect(self.engine_prepared)
        self.events.preparation_progress.connect(self.metrics_progress)
        self.apply_settings()
        self.events.clients.connect(self.show_clients)
        self.events.error.connect(self.show_error)
        self.events.network.connect(self.network_event)
        self.events.prepared.connect(self.prepared)
        self.events.audio.connect(self.audio_started)
        self.events.hotkey.connect(self.hotkey)
        self.reload_engine()
        self.hotkeys = GlobalHotkeys(self.events.hotkey.emit)
        errors = self.hotkeys.configure(self.store.settings.shortcuts, self.store.settings.idle_shortcut_enabled)
        self.idle_binding.setText("=" if self.store.settings.shortcuts["idle"] == "="
                                  else f"Contrôle + Option + {self.store.settings.shortcuts['idle']}")
        if errors:
            self.show_error(" ; ".join(errors))
        elif not self.voices:
            self.show_error("Moteur non préparé. Utilisez « Télécharger / préparer » (connexion nécessaire).")
        QTimer.singleShot(0, self.refresh_clients)

    def build_ui(self):
        root = QWidget()
        root.setObjectName("main_content")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("VOIX DES DOUZE")
        title.setStyleSheet("font-size:26px;font-weight:700;color:#edce88;padding:8px 0")
        layout.addWidget(title)
        layout.addWidget(QLabel("Boussole de récolte · carte et repères · voix des PNJ"))
        source = QGroupBox("Client Dofus à suivre")
        rows = QVBoxLayout(source)
        row = QHBoxLayout()
        self.client_combo = QComboBox()
        self.client_combo.addItem("Ouvrez Dofus puis actualisez…", None)
        row.addWidget(self.client_combo, 1)
        self.button(row, "Actualiser", self.refresh_clients)
        self.prepare_button = self.button(row, "Préparer les données (~90 Mo)", self.prepare_data)
        self.prepare_button.setToolTip("Téléchargement explicite des textes et métadonnées PNJ vérifiés ; rien n’est téléchargé au lancement de l’écoute.")
        rows.addLayout(row)
        control = QHBoxLayout()
        self.start_button = self.button(control, "Démarrer l’écoute", self.start)
        self.pause_button = self.button(control, "Suspendre", self.toggle_pause)
        self.button(control, "Arrêter", self.stop)
        control.addStretch()
        rows.addLayout(control)
        self.network_status = QLabel("Écoute arrêtée · autorisation macOS par session · aucun service permanent")
        self.network_status.setWordWrap(True)
        rows.addWidget(self.network_status)
        layout.addWidget(source)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Gestion de la boussole, de la carte et des voix")
        layout.addWidget(self.tabs, 1)
        page_layouts = []
        for label in ("Carte", "Boussole", "Voix"):
            page = QWidget()
            page.setObjectName("main_content")
            page_layout = QVBoxLayout(page)
            page_layouts.append(page_layout)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, label)
        map_layout, self.compass_layout, voices_layout = page_layouts
        resource_box = QGroupBox("Ressources de la carte · debug réseau")
        resource_layout = QVBoxLayout(resource_box)
        self.resource_summary = QLabel("En attente de la première carte · aucun comptage disponible")
        self.resource_summary.setWordWrap(True)
        self.resource_summary.setAccessibleName("Résumé des ressources détectées")
        resource_layout.addWidget(self.resource_summary)
        self.resource_table = QTableWidget(0, 6)
        self.resource_table.setHorizontalHeaderLabels(["Ressource", "État", "Récoltable par vous", "Case", "Identifiant", "Type"])
        self.resource_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.resource_table.setAccessibleName("Ressources détectées sur la carte courante")
        self.resource_table.setMaximumHeight(170)
        self.resource_table.horizontalHeader().setStretchLastSection(True)
        resource_layout.addWidget(self.resource_table)
        resource_controls = QHBoxLayout()
        self.resource_sound = QCheckBox("Son des ressources")
        self.resource_sound.toggled.connect(self.toggle_resource_sound)
        resource_controls.addWidget(self.resource_sound)
        resource_controls.addWidget(QLabel("Récoltable = mature et action disponible pour ce personnage · aucune lecture d’écran"))
        resource_controls.addStretch()
        self.resource_sound_test = self.button(resource_controls, "Tester le son", self.test_resource_sound)
        resource_layout.addLayout(resource_controls)
        overlay_controls = QHBoxLayout()
        self.overlay_enabled = QCheckBox("Repères sur Dofus")
        self.overlay_enabled.toggled.connect(self.toggle_overlay)
        self.overlay_enabled.setChecked(True)
        overlay_controls.addWidget(self.overlay_enabled)
        self.button(overlay_controls, "Régler les repères…", self.configure_overlay)
        self.overlay_status = QLabel("Plein écran · réglage avec flèches et + / −")
        self.overlay_status.setWordWrap(True)
        overlay_controls.addWidget(self.overlay_status, 1)
        resource_layout.addLayout(overlay_controls)
        self.first_resource = QCheckBox("Premier clic automatique à l’arrivée")
        self.first_resource.setToolTip(
            "Un clic sur une ressource récoltable aléatoire, uniquement si Dofus est au premier plan. "
            "Plein écran, repères réglés et autorisation Accessibilité requis. Aucun pilotage du trajet.")
        self.first_resource.toggled.connect(self.toggle_first_resource)
        resource_layout.addWidget(self.first_resource)
        self.resource_click_debug = QCheckBox("Debug clic · touche : (session uniquement)")
        self.resource_click_debug.toggled.connect(self.toggle_resource_click_debug)
        resource_layout.addWidget(self.resource_click_debug)
        self.resource_click_debug_status = QLabel("Debug désactivé · aucun journal de clic")
        self.resource_click_debug_status.setWordWrap(True)
        self.resource_click_debug_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        resource_layout.addWidget(self.resource_click_debug_status)
        idle_controls = QHBoxLayout()
        self.idle_shortcut_enabled = QCheckBox("Activer le raccourci idle")
        self.idle_shortcut_enabled.setChecked(self.store.settings.idle_shortcut_enabled)
        self.idle_shortcut_enabled.toggled.connect(self.toggle_idle_shortcut)
        idle_controls.addWidget(self.idle_shortcut_enabled)
        self.idle_status = QLabel("Idle désactivé")
        idle_controls.addWidget(self.idle_status)
        self.button(idle_controls, "Raccourci idle…", self.edit_shortcuts)
        self.idle_binding = QLabel()
        idle_controls.addWidget(self.idle_binding)
        resource_layout.addLayout(idle_controls)
        self.idle_measurement = QLabel("Repousse : en attente d’un cycle complet observé.")
        self.idle_measurement.setWordWrap(True)
        resource_layout.addWidget(self.idle_measurement)
        exits = QGroupBox("Changement de carte · zones de clic")
        exit_layout = QVBoxLayout(exits)
        exit_help = QLabel(
            "Définissez les sorties gauche, droite, haut et bas sur l’écran Dofus. "
            "Variation verticale à gauche/droite, horizontale en haut/bas : environ ±3 cm. "
            "Ce réglage ne déclenche aucun déplacement.")
        exit_help.setWordWrap(True)
        exit_layout.addWidget(exit_help)
        self.button(exit_layout, "Régler les sorties de carte…", self.configure_map_exits)
        self.map_exit_status = QLabel()
        self.map_exit_status.setWordWrap(True)
        exit_layout.addWidget(self.map_exit_status)
        self.refresh_map_exit_status()
        map_layout.addWidget(exits)
        circuit = QGroupBox("Circuit de cartes · idle")
        circuit_layout = QVBoxLayout(circuit)
        self.idle_route_status = QLabel()
        self.idle_route_status.setWordWrap(True)
        circuit_layout.addWidget(self.idle_route_status)
        self.button(circuit_layout, "Configurer le circuit idle…", self.configure_idle_route)
        self.refresh_idle_route_status()
        map_layout.addWidget(circuit)
        map_layout.addWidget(resource_box)
        map_layout.addStretch()
        voice_controls = QHBoxLayout()
        self.automatic = QCheckBox("Lecture automatique")
        self.automatic.toggled.connect(self.save_controls)
        voice_controls.addWidget(self.automatic)
        voice_controls.addStretch()
        voices_layout.addLayout(voice_controls)
        self.npc = QLabel("PNJ : —")
        self.npc.setStyleSheet("font-size:20px;font-weight:600;color:#edce88;padding-top:8px")
        voices_layout.addWidget(self.npc)
        body = QHBoxLayout()
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("Le texte du PNJ apparaîtra ici à l’ouverture du dialogue.")
        body.addWidget(self.text, 1)
        voice_box = QGroupBox("Voix")
        voice_box.setMinimumHeight(340)
        form = QFormLayout(voice_box)
        self.engine_combo = QComboBox()
        for key, label in ENGINES.items():
            self.engine_combo.addItem(label, key)
        self.engine_combo.setCurrentIndex(self.engine_combo.findData(self.engine_id))
        self.engine_combo.currentIndexChanged.connect(self.change_engine)
        form.addRow("Moteur vocal", self.engine_combo)
        self.prepare_engine_button = QPushButton("Télécharger / préparer")
        self.prepare_engine_button.setToolTip("Préparation explicite avec Internet ; aucun téléchargement lors d’une simple sélection.")
        self.prepare_engine_button.clicked.connect(self.prepare_engine)
        form.addRow(self.prepare_engine_button)
        self.voice_combo = QComboBox()
        self.default_combo = QComboBox()
        for combo in (self.voice_combo, self.default_combo):
            combo.setMinimumContentsLength(22)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.voice_combo.currentIndexChanged.connect(self.assign_voice)
        self.default_combo.currentIndexChanged.connect(self.save_controls)
        form.addRow("Ce PNJ", self.voice_combo)
        form.addRow("Par défaut", self.default_combo)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.valueChanged.connect(self.save_controls)
        form.addRow("Volume", self.volume)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 2.0)
        self.speed.setSingleStep(0.1)
        self.speed.setSuffix(" ×")
        self.speed.valueChanged.connect(self.save_controls)
        form.addRow("Vitesse", self.speed)
        self.preview_button = QPushButton("Préécouter")
        self.preview_button.clicked.connect(self.preview_voice)
        form.addRow(self.preview_button)
        body.addWidget(voice_box)
        voices_layout.addLayout(body, 1)
        actions = QHBoxLayout()
        self.button(actions, "Lire / Relire", self.replay)
        self.button(actions, "Raccourcis globaux…", self.edit_shortcuts)
        actions.addStretch()
        voices_layout.addLayout(actions)
        self.metrics = QLabel("Synthèse / audio : en attente")
        voices_layout.addWidget(self.metrics)
        self.status = QLabel("Choisissez un client et démarrez l’écoute. Après démarrage, changez de carte puis revenez pour synchroniser les PNJ.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding:12px;background:#233039;border-radius:6px")
        layout.addWidget(self.status)
        self.setStyleSheet("QMainWindow,QDialog,QWidget#main_content {background:#172027;color:#e6e9e8} QWidget {font-size:13px} "
            "QLabel,QCheckBox,QGroupBox {color:#e6e9e8} QGroupBox {border:1px solid #40515b;border-radius:7px;margin-top:12px;padding-top:14px} "
            "QPushButton {padding:8px 12px;border:1px solid #56636b;border-radius:5px;background:#2d4049;color:#f4f1e7} "
            "QPushButton:hover {background:#3b5763} QTextEdit,QComboBox,QDoubleSpinBox {background:#202e36;color:#f4f1e7;padding:5px;border:1px solid #40515b} "
            "QTableWidget {background:#202e36;color:#f4f1e7;gridline-color:#40515b;selection-background-color:#3b5763} "
            "QHeaderView::section {background:#2d4049;color:#f4f1e7;padding:4px;border:1px solid #40515b} "
            "QTabWidget::pane {border:1px solid #40515b} QTabBar::tab {background:#2d4049;color:#f4f1e7;padding:12px 24px} "
            "QTabBar::tab:selected {background:#40515b;color:#edce88;border-bottom:3px solid #edce88}")

    def button(self, layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def apply_settings(self):
        controls = [self.automatic, self.resource_sound, self.first_resource, self.volume, self.speed, self.default_combo]
        for control in controls:
            control.blockSignals(True)
        settings = self.store.settings
        self.automatic.setChecked(settings.automatic)
        self.resource_sound.setChecked(settings.resource_sound)
        self.resource_sound_test.setEnabled(settings.resource_sound)
        self.first_resource.setChecked(settings.first_resource_click)
        self.volume.setValue(round(settings.volume * 100))
        self.speed.setValue(settings.speed)
        index = self.default_combo.findData(self.store.default_for(self.engine_id))
        if index >= 0:
            self.default_combo.setCurrentIndex(index)
        for control in controls:
            control.blockSignals(False)

    def save_controls(self, *_):
        settings = self.store.settings
        settings.automatic = self.automatic.isChecked()
        settings.volume, settings.speed = self.volume.value() / 100, self.speed.value()
        if self.default_combo.currentData():
            self.store.set_default(self.engine_id, self.default_combo.currentData())
        if self.playback is not None:
            self.playback.volume = settings.volume
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")

    def change_engine(self, *_):
        self.engine_id = self.engine_combo.currentData()
        self.store.settings.engine = self.engine_id
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")
        self.reload_engine()

    def reload_engine(self):
        self.tts_generation += 1
        generation = self.tts_generation
        self.tts_ready = False
        self.preview_button.setEnabled(False)
        if self.playback is not None:
            self.playback.close()
        try:
            self.voices = available_voices(self.models, self.engine_id)
        except (OSError, ValueError) as exc:
            self.voices = []
            self.show_error(str(exc))
        self.voice_map = {voice.key: voice for voice in self.voices}
        for combo in (self.voice_combo, self.default_combo):
            combo.blockSignals(True)
            combo.clear()
            for voice in self.voices:
                combo.addItem(voice.label.split(" · ", 1)[-1], voice.key)
            combo.blockSignals(False)
        self.apply_settings()
        if self.last_dialogue and self.voices:
            key = self.store.voice_for(self.last_dialogue.npc, list(self.voice_map), self.engine_id)
            self.voice_combo.blockSignals(True)
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(key))
            self.voice_combo.blockSignals(False)
        self.metrics.setText(f"{ENGINES[self.engine_id]} · chargement et préchauffage…" if self.voices
                             else f"{ENGINES[self.engine_id]} · non préparé · téléchargement explicite nécessaire")
        self.playback = Playback(
            ManagedSpeechEngine(self.models, self.engine_id),
            lambda message: self.events.error.emit(message) if generation == self.tts_generation and not self.shutting_down else None,
            lambda epoch, elapsed: self.events.audio.emit(generation, epoch, elapsed) if not self.shutting_down else None,
            warmup=self.voices,
            ready=lambda: self.events.ready.emit(generation) if not self.shutting_down else None)
        self.playback.volume = self.store.settings.volume

    def engine_ready(self, generation):
        if generation != self.tts_generation or self.shutting_down or self.preparing_engine:
            return
        self.tts_ready = bool(self.voices)
        self.preview_button.setEnabled(self.tts_ready)
        if self.tts_ready:
            self.metrics.setText(f"{ENGINES[self.engine_id]} · prêt · synthèse hors ligne")

    def metrics_progress(self, message):
        if not self.shutting_down:
            self.metrics.setText(message[-300:])

    def prepare_engine(self):
        if self.preparing_engine:
            return
        self.preparing_engine = True
        self.tts_generation += 1
        engine, generation = self.engine_id, self.tts_generation
        self.playback.close()
        self.engine_combo.setEnabled(False)
        self.prepare_engine_button.setEnabled(False)
        self.tts_ready = False
        self.preview_button.setEnabled(False)
        self.metrics.setText("Préparation explicite · installation isolée et téléchargement des poids…")
        script = "prepare_models.py" if engine == "piper" else "prepare_neural.py"
        command = [sys.executable, "-u", str(Path(__file__).resolve().parents[1] / "scripts" / script),
                   "--model-dir", str(self.models)]
        if engine != "piper":
            command += ["--engine", engine]
        def work():
            try:
                with self.preparation_lock:
                    if self.shutting_down:
                        return
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, bufsize=1, start_new_session=True)
                    self.preparation_process = process
                output = ""
                for line in process.stdout:
                    output = (output + line)[-3000:]
                    if not self.shutting_down:
                        self.events.preparation_progress.emit(line.strip())
                process.stdout.close()
                success = process.wait() == 0
                message = "Modèles préparés et vérifiés." if success else output
            except Exception as exc:
                success, message = False, str(exc)
            finally:
                with self.preparation_lock:
                    self.preparation_process = None
            if not self.shutting_down:
                self.events.engine_prepared.emit(generation, success, message)
        threading.Thread(target=work, daemon=True, name="Préparation vocale").start()

    def engine_prepared(self, generation, success, message):
        self.preparing_engine = False
        self.engine_combo.setEnabled(True)
        self.prepare_engine_button.setEnabled(True)
        if generation != self.tts_generation:
            return
        self.status.setText(message if success else f"Préparation impossible : {message}")
        if success:
            self.reload_engine()
        else:
            self.metrics.setText("Moteur non prêt · préparation échouée · aucun remplacement automatique")

    def refresh_clients(self):
        def work():
            try:
                result = clients()
                if not self.shutting_down:
                    self.events.clients.emit(result)
            except Exception as exc:
                if not self.shutting_down:
                    self.events.error.emit(str(exc))
        threading.Thread(target=work, daemon=True).start()

    def show_clients(self, found):
        previous = self.client_combo.currentData()
        self.client_combo.clear()
        self.client_combo.addItem("Choisir un client Dofus…", None)
        for client in found:
            self.client_combo.addItem(client.label, client)
            if previous and client.pid == previous.pid:
                self.client_combo.setCurrentIndex(self.client_combo.count() - 1)
        if len(found) == 1:
            self.client_combo.setCurrentIndex(1)

    def prepare_data(self):
        self.prepare_button.setEnabled(False)
        self.status.setText("Téléchargement explicite et vérification du catalogue français…")
        def work():
            try:
                script = Path(__file__).resolve().parents[1] / "scripts/prepare_network.py"
                result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300)
                success = result.returncode == 0
                message = "Textes et métadonnées PNJ préparés. Vous pouvez démarrer l’écoute." if success else result.stderr[-1200:]
            except Exception as exc:
                success, message = False, str(exc)
            if not self.shutting_down:
                self.events.prepared.emit(success, message)
        threading.Thread(target=work, daemon=True).start()

    def prepared(self, success, message):
        self.prepare_button.setEnabled(True)
        self.status.setText(message if success else f"Attention : {message}")

    def start(self):
        client = self.client_combo.currentData()
        if client is None:
            self.show_error("Ouvrez Dofus, actualisez puis choisissez le client à suivre.")
            return
        if self.listener is not None:
            self.show_error("Attendez la fin de l’écoute précédente avant de la relancer.")
            return
        self.clear_dialogue()
        self.paused = False
        self.pause_button.setText("Suspendre")
        self.generation += 1
        generation = self.generation
        self.accepting = True
        self.start_button.setEnabled(False)
        self.client_combo.setEnabled(False)
        self.network_status.setText(f"Vérification de {client.label} et demande d’autorisation…")
        self.clear_resources()
        self.overlay.pid = client.pid
        self.listener = NetworkSession(client,
            lambda kind, value: self.events.network.emit(generation, kind, value) if not self.shutting_down else None,
            lambda: self.playback.cancel() if generation == self.generation else None)

    def stop(self):
        self.accepting = False
        self.paused = False
        self.pause_button.setText("Suspendre")
        self.playback.cancel()
        self.resource_alert.stop()
        self.clear_resources("Écoute arrêtée · données de ressources effacées")
        self.clear_dialogue()
        if self.listener:
            self.listener.stop()
            self.network_status.setText("Arrêt de l’écoute en cours…")
        else:
            self.network_status.setText("Écoute arrêtée · aucun paquet capturé")

    def toggle_pause(self):
        if self.listener is not None and self.accepting:
            self.stop()
            self.paused = True
            self.pause_button.setText("Reprendre")
            self.status.setText("Suspendu · capture et audio arrêtés. Reprendre demandera une nouvelle écoute.")
        else:
            self.start()

    def network_event(self, generation, kind, value):
        if generation != self.generation:
            return
        if kind == "stopped":
            self.listener = None
            self.accepting = False
            self.start_button.setEnabled(True)
            self.client_combo.setEnabled(True)
            self.network_status.setText("Écoute arrêtée · aucun paquet capturé" if not value else f"Écoute arrêtée · {value}")
            self.clear_dialogue()
            self.resource_alert.stop()
            self.clear_resources("Écoute arrêtée · données de ressources effacées")
            if not self.paused:
                self.status.setText(value or "Écoute terminée. Aucun service ne reste installé ou actif.")
            return
        if not self.accepting or self.listener is None:
            return
        if kind == "authorizing":
            self.network_status.setText(f"Autorisation macOS en attente · {value}")
        elif kind == "ready":
            self.network_status.setText(f"● Écoute active · {value}")
            self.status.setText("Changez de carte puis revenez pour synchroniser les PNJ, puis ouvrez un dialogue.")
        elif kind == "resources":
            self.show_resources(value)
        elif kind == "map_transition":
            self.compass.transition(value)
        elif kind == "map_timing":
            self.compass.timing(value)
        else:
            revision, payload = value
            self.listener.deliver(revision, lambda: self.show_network_dialogue(kind, payload))

    def clear_resources(self, message="En attente de la première carte · aucun comptage disponible"):
        self.resource_summary.setText(message)
        self.resource_table.setRowCount(0)
        self.overlay.set_resources(())
        self.resource_click.clear()
        self.compass.clear()

    def toggle_first_resource(self, enabled):
        self.resource_click.timer.stop()
        self.store.settings.first_resource_click = enabled
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")
        if enabled:
            self.status.setText(
                "Premier clic activé : Dofus plein écran au premier plan, repères réglés et "
                "autorisation macOS Accessibilité requis. Aucun arrêt du trajet garanti.")

    def toggle_resource_sound(self, enabled):
        self.store.settings.resource_sound = enabled
        self.resource_sound_test.setEnabled(enabled)
        if not enabled:
            self.resource_alert.stop()
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")

    def test_resource_sound(self):
        if self.resource_sound.isChecked():
            self.resource_alert.play(self.volume.value() / 100)

    def show_resources(self, snapshot):
        self.compass.observe(snapshot)
        self.overlay.set_resources(snapshot.resources)
        self.resource_click.update(snapshot)
        mature = snapshot.mature_count
        immature = sum(resource.state == 1 for resource in snapshot.resources)
        harvesting = sum(resource.state == 2 for resource in snapshot.resources)
        unknown = len(snapshot.resources) - mature - immature - harvesting
        self.resource_summary.setText(
            f"Carte {snapshot.map_id} · {len(snapshot.resources)} ressources : "
            f"récoltables par vous : {snapshot.harvestable_count} · matures : {mature} · non matures : {immature} · en récolte : {harvesting} · inconnues : {unknown}"
            f" · {snapshot.other_elements} autres éléments · message {snapshot.source}")
        self.resource_table.setRowCount(len(snapshot.resources))
        for row, resource in enumerate(snapshot.resources):
            for column, value in enumerate((resource.name, resource.status, resource.availability,
                                            resource.cell, resource.element_id, resource.type_id)):
                self.resource_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.resource_table.resizeColumnsToContents()
        if snapshot.changed_map and snapshot.harvestable_count:
            self.test_resource_sound()

    def toggle_overlay(self, enabled):
        self.overlay.enabled = enabled
        if enabled and self.overlay.transform is None:
            screen = next((screen for screen in self.screen().virtualSiblings()
                           if screen_key(screen) == self.store.settings.overlay_screen), self.screen())
            saved = self.store.settings.overlay_adjustments.get(screen_key(screen), [0, 0, 1])
            self.overlay.configure(screen.geometry(), saved)
        self.overlay.refresh_visibility()

    def refresh_idle_route_status(self):
        settings = self.store.settings
        route = settings.idle_routes.get(settings.compass_profile, {})
        self.idle_route_status.setText(
            f"Profil : {settings.compass_profile or 'à choisir dans Boussole'} · "
            + (f"Circuit actif · {len(route['maps'])} étapes ordonnées · retour en boucle"
               if route.get("enabled") else "Circuit désactivé · idle reste sur la carte courante"))

    def configure_idle_route(self):
        self.resource_click.stop_idle()
        self.resource_click.timer.stop()
        self.resource_click.cancel_debug_sequence()
        RouteEditor(self).exec()
        self.refresh_idle_route_status()

    def refresh_map_exit_status(self):
        settings = self.store.settings
        profile = settings.map_exits.get(settings.map_exit_screen)
        self.map_exit_status.setText(
            f"4 sorties enregistrées · écran {settings.map_exit_screen} · "
            f"{profile['size'][0]} × {profile['size'][1]} · modifiables à tout moment"
            if profile else "Aucune sortie enregistrée")

    def configure_map_exits(self):
        if self.adjustment is not None or self.exit_calibration is not None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Choisir l’écran des sorties de carte")
        layout = QVBoxLayout(dialog)
        help_text = QLabel(
            "Choisissez l’écran Dofus puis affichez le jeu en plein écran. "
            "Cliquez les quatre points de sortie dans le panneau transparent. "
            "Les segments montrent la variation aléatoire, limitée aux bords de l’écran. "
            "La mesure de 3 cm reste approximative et dépend des informations de l’écran.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        screens = QComboBox()
        selected = self.store.settings.map_exit_screen or self.store.settings.overlay_screen
        for index, screen in enumerate(self.screen().virtualSiblings()):
            screens.addItem(f"Écran {index + 1} · {screen.name()}", screen)
            if screen_key(screen) == selected or (not selected and screen == self.screen()):
                screens.setCurrentIndex(index)
        layout.addWidget(screens)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Placer les sorties")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return
        screen = screens.currentData()
        self.resource_click.stop_idle()
        self.resource_click.timer.stop()
        self.resource_click.cancel_debug_sequence()
        self.overlay.calibrating = True
        self.overlay.hide()
        saved = self.store.settings.map_exits.get(screen_key(screen))
        self.exit_calibration = ExitCalibration(screen, saved)
        self.exit_calibration.finished.connect(
            lambda result: self.finish_map_exits(screen, result))
        self.hide()
        self.exit_calibration.show()
        self.exit_calibration.setFocus()

    def finish_map_exits(self, screen, result):
        self.exit_calibration = None
        self.overlay.calibrating = False
        if not self.shutting_down:
            self.show()
        self.overlay.refresh_visibility()
        if result is None:
            return
        key = screen_key(screen)
        settings = self.store.settings
        previous_screen = settings.map_exit_screen
        previous = settings.map_exits.get(key)
        settings.map_exit_screen = key
        settings.map_exits[key] = result
        try:
            self.store.save()
        except OSError as exc:
            settings.map_exit_screen = previous_screen
            if previous is None:
                settings.map_exits.pop(key)
            else:
                settings.map_exits[key] = previous
            self.show_error(f"Sorties de carte non enregistrées : {exc}")
        self.refresh_map_exit_status()

    def configure_overlay(self):
        if self.adjustment is not None or self.exit_calibration is not None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Régler les repères plein écran")
        layout = QVBoxLayout(dialog)
        instructions = QLabel(
            "Choisissez l’écran Dofus. Flèches : déplacer ; Maj + flèches : plus vite.\n"
            "+ / − : changer l’échelle autour du centre de l’écran.\n"
            "Entrée mémorise le réglage ; Échap annule. Aucun clic ni touche n’est envoyé au jeu.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        screen_combo = QComboBox()
        for index, screen in enumerate(self.screen().virtualSiblings()):
            screen_combo.addItem(f"Écran {index + 1} · {screen.name()}", screen)
            if screen == self.screen():
                screen_combo.setCurrentIndex(index)
        layout.addWidget(screen_combo)
        debug_checkbox = QCheckBox("Debug clavier pour ce réglage uniquement (bandeau + fichier local)")
        debug_checkbox.setToolTip(
            "Désactivé par défaut. Seulement les touches de réglage reçues par le panneau ; "
            "aucun texte ni écoute globale. Arrêt à la fermeture du réglage.")
        layout.addWidget(debug_checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Régler avec le clavier")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if not dialog.exec():
            return
        screen = screen_combo.currentData()
        saved = self.store.settings.overlay_adjustments.get(screen_key(screen), [0, 0, 1])
        self.overlay.calibrating = True
        self.resource_click.stop_idle()
        self.resource_click.timer.stop()
        self.resource_click.cancel_debug_sequence()
        self.overlay.hide()
        self.adjustment = AdjustmentOverlay(
            screen, self.overlay.resources, saved, debug=debug_checkbox.isChecked())
        self.adjustment.finished.connect(
            lambda result: self.finish_overlay_adjustment(screen, result))
        self.hide()
        self.adjustment.show()
        self.adjustment.setFocus()

    def finish_overlay_adjustment(self, screen, result):
        self.adjustment = None
        self.overlay.calibrating = False
        self.show()
        if result is None:
            self.overlay.refresh_visibility()
            return
        key = screen_key(screen)
        previous = self.store.settings.overlay_adjustments.get(key)
        previous_screen = self.store.settings.overlay_screen
        self.store.settings.overlay_screen = key
        self.store.settings.overlay_adjustments[key] = list(result)
        try:
            self.store.save()
        except OSError as exc:
            self.store.settings.overlay_screen = previous_screen
            if previous is None:
                self.store.settings.overlay_adjustments.pop(key)
            else:
                self.store.settings.overlay_adjustments[key] = previous
            self.show_error(f"Enregistrement impossible : {exc}")
            self.overlay.refresh_visibility()
            return
        self.overlay.configure(screen.geometry(), result)
        self.overlay_status.setText("Réglage mémorisé pour cet écran et cette résolution")
        self.overlay_enabled.setChecked(True)
        self.overlay.refresh_visibility()

    def show_network_dialogue(self, kind, value):
        if kind == "dialogue":
            self.last_dialogue = value
            self.npc.setText(f"PNJ : {value.npc}")
            self.text.setPlainText(value.text)
            key = self.store.voice_for(value.npc, list(self.voice_map), self.engine_id) if self.voices else ""
            self.voice_combo.blockSignals(True)
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(key))
            self.voice_combo.blockSignals(False)
            self.metrics.setText("Réplique reçue · synthèse / audio : en attente")
            self.status.setText(f"Dialogue · {value.npc}")
            if self.automatic.isChecked() and self.tts_ready:
                self.speak_dialogue()
        elif kind == "opened":
            self.npc.setText(f"PNJ : {value}")
        elif kind == "synchronized":
            self.clear_dialogue()
            self.status.setText("Carte synchronisée · ouvrez un dialogue PNJ.")
        elif kind == "closed":
            self.clear_dialogue()
        elif kind == "replied":
            self.last_dialogue = None
            self.status.setText("Réponse du joueur détectée · lecture précédente annulée.")
        elif kind == "unsupported":
            self.clear_dialogue()
            self.show_error(value)

    def clear_dialogue(self):
        self.last_dialogue = None
        self.npc.setText("PNJ : —")
        self.text.clear()

    def speak_dialogue(self):
        if not self.tts_ready:
            self.show_error("Le moteur vocal n’est pas prêt. Attendez le chargement ou préparez ses modèles.")
            return
        dialogue = self.last_dialogue
        if not dialogue or not dialogue.text.strip():
            self.show_error("Aucun dialogue reçu à lire.")
            return
        try:
            voice = self.voice_map[self.store.voice_for(dialogue.npc, list(self.voice_map), self.engine_id)]
            self.playback.submit([Utterance(dialogue.text, voice)], self.speed.value())
        except (ValueError, KeyError) as exc:
            self.show_error(str(exc))

    def replay(self):
        self.speak_dialogue()

    def audio_started(self, generation, epoch, elapsed):
        if generation == self.tts_generation and epoch == self.playback.epoch:
            self.metrics.setText(f"Synthèse → lancement audio : {elapsed * 1000:.0f} ms")

    def assign_voice(self, _):
        if self.last_dialogue and self.voice_combo.currentData():
            self.playback.cancel()
            self.store.assign(self.last_dialogue.npc, self.voice_combo.currentData(), self.engine_id)

    def preview_voice(self):
        if not self.tts_ready:
            self.show_error("Le moteur vocal n’est pas encore prêt.")
            return
        voice = self.voice_map.get(self.voice_combo.currentData())
        if voice:
            self.playback.submit([Utterance("Bonjour, je vous accompagne dans le Monde des Douze. Écoutez ma voix avant de me choisir.", voice)], self.speed.value())

    def idle_state_changed(self, text):
        self.idle_status.setText(text)
        if not self.store.settings.compass_enabled:
            memory = self.compass.memory
            snapshot = self.resource_click.snapshot
            if not self.resource_click.idle:
                memory.reset_session()
            elif snapshot is not None and memory.current != snapshot.map_id:
                memory.reset_session()
                memory.observe(snapshot)

    def toggle_idle_shortcut(self, enabled):
        self.store.settings.idle_shortcut_enabled = enabled
        if not enabled:
            self.resource_click.stop_idle()
        errors = self.hotkeys.configure(self.store.settings.shortcuts, enabled)
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")
        if errors:
            self.show_error(" ; ".join(errors))

    def edit_shortcuts(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Raccourcis globaux")
        layout = QFormLayout(dialog)
        combos = {}
        for key, label in [("toggle", "Suspendre / reprendre"), ("stop", "Arrêter"), ("replay", "Lire / relire"), ("idle", "Activer / désactiver idle (Carte)")]:
            combo = QComboBox()
            combo.addItems(list(KEYS))
            if key == "idle":
                combo.insertItem(0, "=")
            combo.setCurrentText(self.store.settings.shortcuts[key])
            combos[key] = combo
            layout.addRow(label, combo)
        layout.addRow(QLabel("= : touche seule pour idle. F1–F12 : Contrôle + Option (Fn peut être nécessaire)."))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            bindings = {key: combo.currentText() for key, combo in combos.items()}
            if len(set(bindings.values())) != len(bindings):
                self.show_error("Choisissez des touches différentes.")
                return
            errors = self.hotkeys.configure(bindings, self.store.settings.idle_shortcut_enabled)
            self.store.settings.shortcuts = bindings
            self.store.save()
            self.idle_binding.setText("=" if bindings["idle"] == "=" else f"Contrôle + Option + {bindings['idle']}")
            self.status.setText(" ; ".join(errors) if errors else "Raccourcis globaux enregistrés.")

    def toggle_resource_click_debug(self, enabled):
        try:
            if enabled:
                self.resource_click.start_debug()
                binding = self.hotkeys.set_resource_debug(True)
                self.resource_click.record("raccourci enregistré", touche=":", liaison=binding)
                self.resource_click_debug_status.setText(
                    f"Touche : → toutes les ressources récoltables (700–1 000 ms) · : pour annuler · croix rose 3 s · journal : {self.resource_click.debug_path}")
            else:
                self.hotkeys.set_resource_debug(False)
                self.resource_click.record("fin")
                self.resource_click.stop_debug()
                self.resource_click_debug_status.setText(
                    f"Debug arrêté · journal conservé : {self.resource_click.debug_path}")
        except (RuntimeError, OSError) as exc:
            self.hotkeys.set_resource_debug(False)
            self.resource_click.stop_debug()
            self.resource_click_debug.blockSignals(True)
            self.resource_click_debug.setChecked(False)
            self.resource_click_debug.blockSignals(False)
            self.resource_click_debug_status.setText(f"Debug indisponible : {exc}")
            self.show_error(str(exc))

    def hotkey(self, action):
        if action == "idle" and not self.store.settings.idle_shortcut_enabled:
            return
        {"toggle": self.toggle_pause, "stop": self.stop, "replay": self.replay,
         "resource_debug": self.resource_click.manual_click,
         "idle": self.resource_click.toggle_idle}[action]()

    def show_error(self, message):
        self.status.setText(f"Attention : {message}")

    def closeEvent(self, event):
        self.shutting_down = True
        if self.exit_calibration is not None:
            self.exit_calibration.close()
        if self.adjustment is not None:
            self.adjustment.finished.disconnect()
            self.adjustment.close()
            self.adjustment = None
        self.overlay.timer.stop()
        self.overlay.close()
        with self.preparation_lock:
            process = self.preparation_process
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        self.stop()
        self.compass.close()
        self.resource_click.record("fin", raison="fermeture")
        self.resource_click.stop_debug()
        self.hotkeys.close()
        self.playback.close()
        self.resource_alert.close()
        event.accept()
