from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget)

from .hotkeys import GlobalHotkeys, KEYS
from .network import NetworkSession, clients
from .playback import Playback, Utterance
from .settings import Store, model_directory
from .speech import SpeechEngine, available_voices


class Events(QObject):
    clients = Signal(object)
    error = Signal(str)
    network = Signal(int, str, object)
    prepared = Signal(bool, str)
    audio = Signal(int, float)
    hotkey = Signal(str)
    ready = Signal()


class MainWindow(QMainWindow):
    def __init__(self, store: Store | None = None, models: Path | None = None):
        super().__init__()
        self.setWindowTitle("Voix des Douze · lecteur local")
        self.resize(960, 720)
        self.store = store or Store()
        self.events = Events()
        self.voices = available_voices(models or model_directory())
        self.voice_map = {voice.key: voice for voice in self.voices}
        self.listener = None
        self.generation = 0
        self.accepting = False
        self.paused = False
        self.last_dialogue = None
        self.shutting_down = False
        self.build_ui()
        self.playback = Playback(SpeechEngine(models or model_directory()), self.events.error.emit,
            self.events.audio.emit, warmup=self.voices, ready=self.events.ready.emit)
        self.playback.volume = self.store.settings.volume
        self.apply_settings()
        self.events.clients.connect(self.show_clients)
        self.events.error.connect(self.show_error)
        self.events.network.connect(self.network_event)
        self.events.prepared.connect(self.prepared)
        self.events.audio.connect(self.audio_started)
        self.events.hotkey.connect(self.hotkey)
        self.events.ready.connect(lambda: self.metrics.setText(
            "Voix préchauffées · en attente d’un dialogue" if self.voices else "Aucun modèle vocal installé"))
        self.hotkeys = GlobalHotkeys(self.events.hotkey.emit)
        errors = self.hotkeys.configure(self.store.settings.shortcuts)
        if errors:
            self.show_error(" ; ".join(errors))
        elif not self.voices:
            self.show_error("Aucun modèle vocal préparé. Fermez puis lancez Installer.command.")
        QTimer.singleShot(0, self.refresh_clients)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("VOIX DES DOUZE")
        title.setStyleSheet("font-size:26px;font-weight:700;color:#edce88;padding:8px 0")
        layout.addWidget(title)
        layout.addWidget(QLabel("Dialogues locaux · voix françaises Piper · aucune action envoyée au jeu"))
        source = QGroupBox("Client Dofus à suivre")
        rows = QVBoxLayout(source)
        row = QHBoxLayout()
        self.client_combo = QComboBox()
        self.client_combo.addItem("Ouvrez Dofus puis actualisez…", None)
        row.addWidget(self.client_combo, 1)
        self.button(row, "Actualiser", self.refresh_clients)
        self.prepare_button = self.button(row, "Préparer les textes (~90 Mo)", self.prepare_data)
        self.prepare_button.setToolTip("Téléchargement explicite des textes français vérifiés ; rien n’est téléchargé au lancement de l’écoute.")
        rows.addLayout(row)
        control = QHBoxLayout()
        self.start_button = self.button(control, "Démarrer l’écoute", self.start)
        self.pause_button = self.button(control, "Suspendre", self.toggle_pause)
        self.button(control, "Arrêter", self.stop)
        control.addStretch()
        self.automatic = QCheckBox("Lecture automatique")
        self.automatic.toggled.connect(self.save_controls)
        control.addWidget(self.automatic)
        rows.addLayout(control)
        self.network_status = QLabel("Écoute arrêtée · autorisation macOS par session · aucun service permanent")
        self.network_status.setWordWrap(True)
        rows.addWidget(self.network_status)
        layout.addWidget(source)
        self.npc = QLabel("PNJ : —")
        self.npc.setStyleSheet("font-size:20px;font-weight:600;color:#edce88;padding-top:8px")
        layout.addWidget(self.npc)
        body = QHBoxLayout()
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("Le texte du PNJ apparaîtra ici à l’ouverture du dialogue.")
        body.addWidget(self.text, 1)
        voice_box = QGroupBox("Voix")
        form = QFormLayout(voice_box)
        self.voice_combo = QComboBox()
        self.default_combo = QComboBox()
        for voice in self.voices:
            self.voice_combo.addItem(voice.label, voice.key)
            self.default_combo.addItem(voice.label, voice.key)
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
        preview = QPushButton("Préécouter")
        preview.clicked.connect(self.preview_voice)
        form.addRow(preview)
        body.addWidget(voice_box)
        layout.addLayout(body, 1)
        actions = QHBoxLayout()
        self.button(actions, "Lire / Relire", self.replay)
        self.button(actions, "Raccourcis globaux…", self.edit_shortcuts)
        actions.addStretch()
        layout.addLayout(actions)
        self.metrics = QLabel("Synthèse / audio : en attente")
        layout.addWidget(self.metrics)
        self.status = QLabel("Choisissez un client et démarrez l’écoute. Après démarrage, changez de carte puis revenez pour synchroniser les PNJ.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding:12px;background:#233039;border-radius:6px")
        layout.addWidget(self.status)
        self.setStyleSheet("QMainWindow,QDialog {background:#172027;color:#e6e9e8} QWidget {font-size:13px} "
            "QLabel,QCheckBox,QGroupBox {color:#e6e9e8} QGroupBox {border:1px solid #40515b;border-radius:7px;margin-top:12px;padding-top:14px} "
            "QPushButton {padding:8px 12px;border:1px solid #56636b;border-radius:5px;background:#2d4049;color:#f4f1e7} "
            "QPushButton:hover {background:#3b5763} QTextEdit,QComboBox,QDoubleSpinBox {background:#202e36;color:#f4f1e7;padding:5px;border:1px solid #40515b}")

    def button(self, layout, label, callback):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def apply_settings(self):
        controls = [self.automatic, self.volume, self.speed, self.default_combo]
        for control in controls:
            control.blockSignals(True)
        settings = self.store.settings
        self.automatic.setChecked(settings.automatic)
        self.volume.setValue(round(settings.volume * 100))
        self.speed.setValue(settings.speed)
        index = self.default_combo.findData(settings.default_voice)
        if index >= 0:
            self.default_combo.setCurrentIndex(index)
        for control in controls:
            control.blockSignals(False)

    def save_controls(self, *_):
        settings = self.store.settings
        settings.automatic = self.automatic.isChecked()
        settings.volume, settings.speed = self.volume.value() / 100, self.speed.value()
        settings.default_voice = self.default_combo.currentData() or ""
        self.playback.volume = settings.volume
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")

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
                message = "Textes préparés. Vous pouvez démarrer l’écoute." if success else result.stderr[-1200:]
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
        self.listener = NetworkSession(client,
            lambda kind, value: self.events.network.emit(generation, kind, value) if not self.shutting_down else None,
            lambda: self.playback.cancel() if generation == self.generation else None)

    def stop(self):
        self.accepting = False
        self.paused = False
        self.pause_button.setText("Suspendre")
        self.playback.cancel()
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
        else:
            revision, payload = value
            self.listener.deliver(revision, lambda: self.show_network_dialogue(kind, payload))

    def show_network_dialogue(self, kind, value):
        if kind == "dialogue":
            self.last_dialogue = value
            self.npc.setText(f"PNJ : {value.npc}")
            self.text.setPlainText(value.text)
            key = self.store.voice_for(value.npc, list(self.voice_map)) if self.voices else ""
            self.voice_combo.blockSignals(True)
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(key))
            self.voice_combo.blockSignals(False)
            self.metrics.setText("Réplique reçue · synthèse / audio : en attente")
            self.status.setText(f"Dialogue · {value.npc}")
            if self.automatic.isChecked() and self.voices:
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
        dialogue = self.last_dialogue
        if not dialogue or not dialogue.text.strip():
            self.show_error("Aucun dialogue reçu à lire.")
            return
        try:
            voice = self.voice_map[self.store.voice_for(dialogue.npc, list(self.voice_map))]
            self.playback.submit([Utterance(dialogue.text, voice)], self.speed.value())
        except (ValueError, KeyError) as exc:
            self.show_error(str(exc))

    def replay(self):
        self.speak_dialogue()

    def audio_started(self, epoch, elapsed):
        if epoch == self.playback.epoch:
            self.metrics.setText(f"Synthèse → lancement audio : {elapsed * 1000:.0f} ms")

    def assign_voice(self, _):
        if self.last_dialogue and self.voice_combo.currentData():
            self.playback.cancel()
            self.store.assign(self.last_dialogue.npc, self.voice_combo.currentData())

    def preview_voice(self):
        voice = self.voice_map.get(self.voice_combo.currentData())
        if voice:
            self.playback.submit([Utterance("Bonjour, je vous accompagne dans le Monde des Douze. Écoutez ma voix avant de me choisir.", voice)], self.speed.value())

    def edit_shortcuts(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Raccourcis globaux — Contrôle + Option + touche")
        layout = QFormLayout(dialog)
        combos = {}
        for key, label in [("toggle", "Suspendre / reprendre"), ("stop", "Arrêter"), ("replay", "Lire / relire")]:
            combo = QComboBox()
            combo.addItems(list(KEYS))
            combo.setCurrentText(self.store.settings.shortcuts[key])
            combos[key] = combo
            layout.addRow(label, combo)
        layout.addRow(QLabel("Touches F1–F12 : Fn peut être nécessaire.\nAucune frappe de jeu n’est observée ou automatisée."))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            bindings = {key: combo.currentText() for key, combo in combos.items()}
            if len(set(bindings.values())) != len(bindings):
                self.show_error("Choisissez trois touches différentes.")
                return
            errors = self.hotkeys.configure(bindings)
            self.store.settings.shortcuts = bindings
            self.store.save()
            self.status.setText(" ; ".join(errors) if errors else "Raccourcis globaux enregistrés (Contrôle + Option).")

    def hotkey(self, action):
        {"toggle": self.toggle_pause, "stop": self.stop, "replay": self.replay}[action]()

    def show_error(self, message):
        self.status.setText(f"Attention : {message}")

    def closeEvent(self, event):
        self.shutting_down = True
        self.stop()
        self.hotkeys.close()
        self.playback.close()
        event.accept()
