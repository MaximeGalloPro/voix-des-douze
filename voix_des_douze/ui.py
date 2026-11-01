from __future__ import annotations

from pathlib import Path
import threading
import subprocess
import sys

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSlider, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from . import macos
from .demo import DemoSource, REGIONS, render_slide
from .hotkeys import GlobalHotkeys, KEYS
from .monitor import Monitor
from .network import NetworkSession, clients as network_clients
from .playback import Playback, Utterance
from .settings import Store, model_directory
from .speech import SpeechEngine, available_voices
from .vision import Rect, recognize


class Events(QObject):
    windows = Signal(object)
    error = Signal(str)
    frame = Signal(int, object, object)
    dialogue = Signal(int, object, bool, float, int)
    audio = Signal(int, float)
    hotkey = Signal(str)
    snapshot = Signal(object)
    ready = Signal()
    vision_ready = Signal()
    clients = Signal(object)
    network = Signal(int, str, object)
    network_prepared = Signal(bool, str)


class Preview(QWidget):
    """All selections use the displayed image bounds, never desktop logical points."""
    selected = Signal(object)

    def __init__(self, editable=False):
        super().__init__()
        self.setMinimumSize(400, 260)
        self.pixmap = QPixmap()
        self.regions = {}
        self.editable = editable
        self.origin = None
        self.drag = None
        self.active_region = "text"

    def set_image(self, image, regions):
        self.pixmap = QPixmap.fromImage(ImageQt(image.convert("RGB")))
        self.regions = regions
        self.update()

    def bounds(self):
        if self.pixmap.isNull():
            return QRectF()
        scale = min(self.width() / self.pixmap.width(), self.height() / self.pixmap.height())
        w, h = self.pixmap.width() * scale, self.pixmap.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171b22"))
        bounds = self.bounds()
        if bounds.isEmpty():
            painter.setPen(QColor("#aeb9c9"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Aperçu de la seule fenêtre sélectionnée")
            return
        painter.drawPixmap(bounds, self.pixmap, QRectF(self.pixmap.rect()))
        colors = {"npc": "#e8b85c", "text": "#6edbc5", "responses": "#92b9ff"}
        labels = {"npc": "PNJ", "text": "Dialogue", "responses": "Réponses"}
        for key, region in self.regions.items():
            rectangle = QRectF(bounds.x() + region.x * bounds.width(), bounds.y() + region.y * bounds.height(),
                               region.width * bounds.width(), region.height * bounds.height())
            painter.setPen(QPen(QColor(colors.get(key, "white")), 2))
            painter.drawRect(rectangle)
            painter.drawText(rectangle.topLeft() + QPointF(4, 16), labels.get(key, key))
        if self.drag is not None:
            painter.setPen(QPen(QColor("white"), 2, Qt.DashLine))
            painter.drawRect(self.drag)

    def mousePressEvent(self, event):
        if self.editable and self.bounds().contains(event.position()):
            self.origin = event.position()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.drag = QRectF(self.origin, event.position()).normalized().intersected(self.bounds())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.origin is None:
            return
        bounds = self.bounds()
        rectangle = QRectF(self.origin, event.position()).normalized().intersected(bounds)
        if rectangle.width() >= 8 and rectangle.height() >= 8:
            self.regions[self.active_region] = Rect((rectangle.x() - bounds.x()) / bounds.width(),
                (rectangle.y() - bounds.y()) / bounds.height(), rectangle.width() / bounds.width(), rectangle.height() / bounds.height())
            self.selected.emit(self.regions)
        self.origin = self.drag = None
        self.update()


class RegionDialog(QDialog):
    def __init__(self, image, regions, parent):
        super().__init__(parent)
        self.setWindowTitle("Délimiter les zones — coordonnées relatives à la fenêtre")
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choisissez une zone puis tracez son rectangle. Les zones suivent la taille de la fenêtre.\n"
                               "Pour des panneaux qui se déplacent, préférez le repérage automatique."))
        selector = QComboBox()
        for label, key in [("Nom du PNJ", "npc"), ("Dialogue", "text"), ("Réponses (facultatif)", "responses")]:
            selector.addItem(label, key)
        layout.addWidget(selector)
        self.preview = Preview(editable=True)
        self.preview.active_region = "npc"
        self.preview.set_image(image, dict(regions))
        selector.currentIndexChanged.connect(lambda _: setattr(self.preview, "active_region", selector.currentData()))
        layout.addWidget(self.preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if "text" not in self.preview.regions:
            QMessageBox.warning(self, "Zone manquante", "Tracez au moins la zone du dialogue.")
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, store: Store | None = None, models: Path | None = None):
        super().__init__()
        self.setWindowTitle("Voix des Douze · lecteur local")
        self.resize(1200, 800)
        self.store = store or Store()
        self.events = Events()
        self.models = models or model_directory()
        self.voices = available_voices(self.models)
        self.voice_map = {v.key: v for v in self.voices}
        self.monitor = None
        self.session = 0
        self.network = None
        self.network_token = 0
        self.network_mode = False
        self.last_dialogue = None
        self.last_image = None
        self.last_regions = {}
        self.source = None
        self.source_key = ""
        self.demo = None
        self.paused = False
        self.vision_preheated = False
        self.shutting_down = False
        self.playback = Playback(SpeechEngine(self.models), self.events.error.emit, self.events.audio.emit,
                                 warmup=self.voices, ready=self.events.ready.emit)
        self.playback.volume = self.store.settings.volume
        self.events.windows.connect(self.show_windows)
        self.events.error.connect(self.show_error)
        self.events.frame.connect(self.show_frame)
        self.events.dialogue.connect(self.show_dialogue)
        self.events.audio.connect(self.audio_started)
        self.events.hotkey.connect(self.hotkey)
        self.events.snapshot.connect(self.edit_snapshot)
        self.events.ready.connect(lambda: self.metrics.setText(
            "Piper préchauffé • OCR / démarrage audio : en attente" if self.voices else "Aucun modèle vocal installé"))
        self.events.vision_ready.connect(self.vision_prepared)
        self.events.clients.connect(self.show_clients)
        self.events.network.connect(self.network_event)
        self.events.network_prepared.connect(self.network_prepared)
        self.build_ui()
        self.apply_settings()
        self.hotkeys = GlobalHotkeys(self.events.hotkey.emit)
        errors = self.hotkeys.configure(self.store.settings.shortcuts)
        if errors:
            self.show_error(" ; ".join(errors))
        elif not self.voices:
            self.show_error("Aucun modèle vocal préparé. Fermez puis lancez Installer.command.")
        QTimer.singleShot(0, self.refresh_windows)
        self.background(lambda: recognize(render_slide(0), REGIONS), lambda _: self.events.vision_ready.emit())

    def vision_prepared(self):
        self.vision_preheated = True

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("VOIX DES DOUZE")
        title.setStyleSheet("font-size: 26px; font-weight: 700; color: #edce88; padding: 8px 0")
        layout.addWidget(title)
        layout.addWidget(QLabel("Lecture locale • Apple Vision français • Piper CPU • aucune action envoyée au jeu"))
        source_box = QGroupBox("1 · Source — une seule fenêtre à la fois")
        source_rows = QVBoxLayout(source_box)
        source_layout = QHBoxLayout()
        source_rows.addLayout(source_layout)
        self.window_combo = QComboBox()
        self.window_combo.addItem("Choisir une fenêtre Dofus…", None)
        self.window_combo.setMinimumWidth(340)
        self.window_combo.currentIndexChanged.connect(self.select_window)
        source_layout.addWidget(self.window_combo, 1)
        self.button(source_layout, "Actualiser", self.refresh_windows)
        self.button(source_layout, "Autorisations…", self.permissions)
        captures = QHBoxLayout()
        source_rows.addLayout(captures)
        self.button(captures, "Ouvrir une capture…", self.open_image)
        test_button = QPushButton("Captures de test")
        test_button.setToolTip("Captures réelles recadrées : OCR et voix, sans accès écran ni Dofus ouvert.")
        test_menu = QMenu(test_button)
        fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dialogues"
        for label, filename, origin in (
            ("Kerubim Crépin", "kerubim.png", (1290, 370)),
            ("Nacf Virgil", "nacf.png", (750, 480)),
            ("Al Etsop", "al.png", (1290, 330)),
        ):
            action = test_menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, path=fixtures / filename, position=origin:
                    self.load_image(path, test_origin=position))
        test_button.setMenu(test_menu)
        captures.addWidget(test_button)
        self.button(captures, "Démonstration", self.start_demo)
        captures.addStretch()
        network_row = QHBoxLayout()
        source_rows.addLayout(network_row)
        self.client_combo = QComboBox()
        self.client_combo.addItem("Choisir un client Dofus pour le réseau…", None)
        self.client_combo.setMinimumWidth(280)
        network_row.addWidget(self.client_combo, 1)
        self.network_button = self.button(network_row, "Démarrer l’écoute réseau", self.start_network)
        self.network_prepare_button = self.button(network_row, "Préparer données réseau (~90 Mo)", self.prepare_network)
        self.network_prepare_button.setToolTip("Téléchargement explicite du catalogue français vérifié. Aucun téléchargement au démarrage de l’écoute.")
        self.network_status = QLabel("Réseau arrêté · autorisation administrateur par session · aucun service permanent")
        self.network_status.setWordWrap(True)
        source_rows.addWidget(self.network_status)
        layout.addWidget(source_box)
        control = QHBoxLayout()
        self.start_button = self.button(control, "Démarrer OCR", self.start)
        self.pause_button = self.button(control, "Suspendre", self.toggle_pause)
        self.button(control, "Arrêter", self.stop)
        self.button(control, "Lire / Relire", self.replay)
        self.button(control, "Dialogue démo suivant", self.next_demo)
        control.addStretch()
        self.automatic = QCheckBox("Lecture automatique")
        self.automatic.toggled.connect(self.save_controls)
        control.addWidget(self.automatic)
        layout.addLayout(control)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.preview = Preview()
        left_layout.addWidget(self.preview, 1)
        zones = QHBoxLayout()
        self.auto_regions = QCheckBox("Repérage automatique des panneaux")
        self.auto_regions.setChecked(True)
        self.auto_regions.toggled.connect(self.regions_changed)
        zones.addWidget(self.auto_regions)
        self.button(zones, "Délimiter les zones…", self.edit_regions)
        left_layout.addLayout(zones)
        split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.npc = QLabel("PNJ : —")
        self.npc.setStyleSheet("font-size: 20px; font-weight: 600; color: #edce88")
        right_layout.addWidget(self.npc)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("Le texte du dialogue apparaîtra ici — OCR ou réseau selon le mode choisi.")
        right_layout.addWidget(self.text, 2)
        self.responses = QTextEdit()
        self.responses.setReadOnly(True)
        self.responses.setPlaceholderText("Réponses reconnues — non lues par défaut")
        self.responses.setMaximumHeight(130)
        right_layout.addWidget(self.responses)
        form = QFormLayout()
        self.voice_combo = QComboBox()
        self.default_combo = QComboBox()
        self.response_combo = QComboBox()
        for voice in self.voices:
            for combo in [self.voice_combo, self.default_combo, self.response_combo]:
                combo.addItem(voice.label, voice.key)
        self.voice_combo.currentIndexChanged.connect(self.assign_voice)
        self.default_combo.currentIndexChanged.connect(self.save_controls)
        self.response_combo.currentIndexChanged.connect(self.save_controls)
        form.addRow("Voix de ce PNJ", self.voice_combo)
        form.addRow("Voix de secours", self.default_combo)
        form.addRow("Voix des réponses", self.response_combo)
        self.read_responses = QCheckBox("Lire les réponses après le dialogue")
        self.read_responses.toggled.connect(self.save_controls)
        form.addRow(self.read_responses)
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
        right_layout.addLayout(form)
        voice_buttons = QHBoxLayout()
        self.button(voice_buttons, "Préécouter la voix", self.preview_voice)
        self.button(voice_buttons, "Raccourcis globaux…", self.edit_shortcuts)
        right_layout.addLayout(voice_buttons)
        split.addWidget(right)
        split.setSizes([650, 450])
        layout.addWidget(split, 1)
        self.metrics = QLabel("Mesures : en attente — le démarrage afplay ne prouve pas l’instant acoustique exact.")
        layout.addWidget(self.metrics)
        self.status = QLabel("Prêt. Sélectionnez une fenêtre Dofus, une capture ou la démonstration.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding: 12px; background: #233039; border-radius: 6px")
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
        controls = [self.automatic, self.read_responses, self.volume, self.speed, self.default_combo, self.response_combo]
        for widget in controls:
            widget.blockSignals(True)
        s = self.store.settings
        self.automatic.setChecked(s.automatic)
        self.read_responses.setChecked(s.read_responses)
        self.volume.setValue(round(s.volume * 100))
        self.speed.setValue(s.speed)
        for combo, key in [(self.default_combo, s.default_voice), (self.response_combo, s.response_voice)]:
            index = combo.findData(key)
            if index >= 0:
                combo.setCurrentIndex(index)
        if self.response_combo.count() > 1 and not s.response_voice:
            self.response_combo.setCurrentIndex(1)
        for widget in controls:
            widget.blockSignals(False)

    def save_controls(self, *_):
        s = self.store.settings
        s.automatic, s.read_responses = self.automatic.isChecked(), self.read_responses.isChecked()
        s.volume, s.speed = self.volume.value() / 100, self.speed.value()
        s.default_voice, s.response_voice = self.default_combo.currentData() or "", self.response_combo.currentData() or ""
        self.playback.volume = s.volume
        try:
            self.store.save()
        except OSError as exc:
            self.show_error(f"Enregistrement impossible : {exc}")

    def background(self, work, complete):
        def run():
            try:
                result = work()
                if not self.shutting_down:
                    complete(result)
            except Exception as exc:
                if not self.shutting_down:
                    self.events.error.emit(str(exc))
        threading.Thread(target=run, daemon=True).start()

    def show_clients(self, clients):
        previous = self.client_combo.currentData()
        self.client_combo.blockSignals(True)
        self.client_combo.clear()
        self.client_combo.addItem("Choisir un client Dofus pour le réseau…", None)
        for client in clients:
            self.client_combo.addItem(client.label, client)
            if previous and client.pid == previous.pid:
                self.client_combo.setCurrentIndex(self.client_combo.count() - 1)
        if len(clients) == 1 and self.client_combo.currentIndex() == 0:
            self.client_combo.setCurrentIndex(1)
        self.client_combo.blockSignals(False)

    def prepare_network(self):
        self.network_prepare_button.setEnabled(False)
        self.network_status.setText("Téléchargement explicite et vérification du catalogue français…")

        def work():
            try:
                script = Path(__file__).resolve().parents[1] / "scripts/prepare_network.py"
                result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300)
                success = result.returncode == 0
                message = "Catalogue réseau préparé. Sélectionnez le client puis démarrez l’écoute." if success else result.stderr[-1200:]
            except Exception as exc:
                success, message = False, str(exc)
            if not self.shutting_down:
                self.events.network_prepared.emit(success, message)
        threading.Thread(target=work, daemon=True).start()

    def network_prepared(self, success, message):
        self.network_prepare_button.setEnabled(True)
        self.network_status.setText(message)
        if not success:
            self.show_error(message)

    def start_network(self):
        client = self.client_combo.currentData()
        if client is None:
            self.show_error("Ouvrez Dofus, actualisez puis choisissez le client réseau à suivre.")
            return
        if self.network is not None:
            self.show_error("Attendez la fin de l’écoute précédente avant de la relancer.")
            return
        self.stop()
        self.clear_dialogue()
        self.network_mode = True
        self.session += 1
        session = self.network_token = self.session
        self.network_button.setEnabled(False)
        self.client_combo.setEnabled(False)
        self.read_responses.setEnabled(False)
        self.network_status.setText(f"Vérification de {client.label} et demande d’autorisation…")
        self.network = NetworkSession(client,
            lambda kind, value: self.events.network.emit(session, kind, value) if not self.shutting_down else None,
            lambda: self.playback.cancel() if session == self.session else None)

    def network_event(self, token, kind, value):
        if token != self.network_token:
            return
        if kind == "stopped":
            self.network = None
            self.network_button.setEnabled(True)
            self.client_combo.setEnabled(True)
            self.read_responses.setEnabled(True)
            self.network_status.setText("Réseau arrêté · aucun paquet capturé" if not value else f"Réseau arrêté · {value}")
            if token == self.session:
                self.clear_dialogue()
                self.status.setText(value or "Écoute réseau terminée.")
            return
        if token != self.session or self.network is None:
            return
        if kind == "authorizing":
            self.network_status.setText(f"Autorisation macOS en attente · {value}")
        elif kind == "ready":
            self.network_status.setText(f"● Écoute active · {value}")
            self.status.setText("Changez de carte puis revenez pour synchroniser les PNJ, puis ouvrez un dialogue. Aucun OCR ni enregistrement des paquets.")
        else:
            revision, payload = value
            self.network.deliver(revision, lambda: self.show_network_dialogue(kind, payload))

    def show_network_dialogue(self, kind, value):
        if kind == "dialogue":
            self.present_dialogue(value, True, "Réseau : réplique reçue sans OCR")
        elif kind == "opened":
            self.npc.setText(f"PNJ : {value}")
            self.status.setText(f"Dialogue ouvert · {value}")
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

    def refresh_windows(self):
        self.background(network_clients, self.events.clients.emit)
        self.status.setText("Recherche des fenêtres…")
        self.background(macos.list_windows, self.events.windows.emit)

    def show_windows(self, windows):
        self.window_combo.blockSignals(True)
        previous = self.window_combo.currentData()
        self.window_combo.clear()
        self.window_combo.addItem("Choisir une fenêtre Dofus…", None)
        for window in sorted(windows, key=lambda w: ("dofus" not in (w.app + w.title).lower(), w.app, w.title)):
            self.window_combo.addItem(f"{window.app} — {window.title} [{window.window_id}]", window)
        if previous:
            for i in range(self.window_combo.count()):
                candidate = self.window_combo.itemData(i)
                if candidate and candidate.window_id == previous.window_id:
                    self.window_combo.setCurrentIndex(i)
                    break
        self.window_combo.blockSignals(False)
        self.status.setText("Fenêtres actualisées. Sélectionnez celle à lire ; aucune autre ne sera surveillée.")

    def select_window(self, _):
        window = self.window_combo.currentData()
        self.stop()
        self.clear_dialogue()
        if window is None:
            self.source = None
            return
        self.demo = None
        self.source = lambda: macos.capture_window(window.window_id)
        self.source_key = f"{window.app}:{window.title}"
        self.restore_regions()
        self.status.setText(f"Fenêtre choisie : {window.title}. Cliquez sur Démarrer.")

    def restore_regions(self):
        self.auto_regions.blockSignals(True)
        self.auto_regions.setChecked(self.source_key not in self.store.settings.regions)
        self.auto_regions.blockSignals(False)

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Capture Dofus", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            self.load_image(Path(path))

    def load_image(self, path, *, test_origin=None):
        try:
            with Image.open(path) as raw:
                image = raw.convert("RGB")
            if test_origin is not None:
                # Same neutral canvas as the OCR benchmarks; private chat and
                # account names remain excluded from the repository fixtures.
                canvas = Image.new("RGB", (2048, 1280))
                canvas.paste(image, test_origin)
                canvas.info.update(image.info)
                image = canvas
            self.stop()
            self.clear_dialogue()
            self.demo = None
            self.source = lambda: image.copy()
            self.source_key = f"test:{Path(path).stem}" if test_origin is not None else f"image:{Path(path).resolve()}"
            self.restore_regions()
            self.start()
            self.status.setText(
                f"Capture de test : {Path(path).stem} — image réelle recadrée, pas de Dofus en direct."
                if test_origin is not None else
                "Capture fixe réelle — ce mode ne constitue pas un test Dofus en direct.")
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))

    def start_demo(self):
        self.stop()
        self.clear_dialogue()
        self.demo = DemoSource()
        self.source = self.demo
        self.source_key = "demo"
        self.restore_regions()
        self.start()
        self.status.setText("Démonstration sur images synthétiques • changement toutes les 12 secondes.")

    def next_demo(self):
        if self.demo:
            self.demo.next()

    def configured_regions(self):
        if self.auto_regions.isChecked():
            return REGIONS if self.demo else None
        stored = self.store.settings.regions.get(self.source_key, {})
        return {key: Rect(*value) for key, value in stored.items()} or (REGIONS if self.demo else None)

    def start(self):
        if self.source is None:
            self.show_error("Choisissez d’abord une fenêtre, une capture ou la démonstration.")
            return
        self.stop()
        self.network_mode = False
        self.paused = False
        self.pause_button.setText("Suspendre")
        self.session += 1
        session = self.session
        self.monitor = Monitor(self.source, self.configured_regions(),
            lambda dialogue, fresh, elapsed, revision: self.events.dialogue.emit(session, dialogue, fresh, elapsed, revision),
            lambda: self.playback.cancel() if session == self.session else None,
            lambda image, regions: self.events.frame.emit(session, image, regions),
            lambda error: self.events.error.emit(error) if session == self.session else None)
        self.status.setText("Surveillance active • seule la source choisie est capturée, même en arrière-plan.")

    def stop(self):
        self.session += 1
        if self.monitor:
            self.monitor.stop()
            self.monitor = None
        if self.network:
            self.network.stop()
            self.last_dialogue = None
            self.network_status.setText("Arrêt de l’écoute réseau en cours…")
        self.playback.cancel()
        self.paused = False
        self.pause_button.setText("Suspendre")
        self.status.setText("Arrêté. Aucune ancienne génération ne peut reprendre la lecture.")

    def toggle_pause(self):
        if self.monitor or self.network:
            self.stop()
            self.paused = True
            self.pause_button.setText("Reprendre")
            self.status.setText("Suspendu · capture et audio arrêtés. Reprendre demandera une nouvelle écoute.")
        else:
            self.start_network() if self.network_mode else self.start()

    def regions_changed(self):
        if self.auto_regions.isChecked() and self.source_key in self.store.settings.regions:
            del self.store.settings.regions[self.source_key]
            self.store.save()
        elif not self.auto_regions.isChecked() and self.source_key not in self.store.settings.regions:
            if not self.last_regions:
                self.auto_regions.blockSignals(True)
                self.auto_regions.setChecked(True)
                self.auto_regions.blockSignals(False)
                self.show_error("Utilisez Délimiter les zones pour définir les rectangles manuels.")
                return
            self.store.settings.regions[self.source_key] = {
                key: [r.x, r.y, r.width, r.height] for key, r in self.last_regions.items()}
            self.store.save()
        if self.monitor:
            self.start()

    def show_frame(self, session, image, regions):
        if session != self.session:
            return
        self.last_image, self.last_regions = image, regions
        self.preview.set_image(image, regions)

    def clear_dialogue(self):
        self.last_dialogue = None
        self.last_image = None
        self.last_regions = {}
        self.preview.pixmap = QPixmap()
        self.preview.update()
        self.npc.setText("PNJ : —")
        self.text.clear()
        self.responses.clear()

    def show_dialogue(self, session, dialogue, fresh, elapsed, revision):
        if session != self.session or self.monitor is None or revision != self.monitor.revision:
            return
        self.present_dialogue(dialogue, fresh, f"OCR : {elapsed * 1000:.0f} ms")

    def present_dialogue(self, dialogue, fresh, metric):
        self.last_dialogue = dialogue
        self.npc.setText(f"PNJ : {dialogue.npc or 'non reconnu — voix de secours'}")
        self.text.setPlainText(dialogue.text)
        self.responses.setPlainText("\n".join(dialogue.responses))
        self.metrics.setText(metric + " • synthèse / audio : en attente")
        if dialogue.text and self.voices:
            key = self.store.voice_for(dialogue.npc, list(self.voice_map))
            self.voice_combo.blockSignals(True)
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(key))
            self.voice_combo.blockSignals(False)
            if self.response_combo.currentData() == key and len(self.voices) > 1:
                alternate = next(v.key for v in self.voices if v.key != key)
                self.response_combo.setCurrentIndex(self.response_combo.findData(alternate))
            if fresh and self.automatic.isChecked():
                self.speak_dialogue()
        elif not dialogue.text:
            self.playback.cancel()

    def speak_dialogue(self):
        dialogue = self.last_dialogue
        if not dialogue or not dialogue.text.strip():
            self.show_error("Aucun dialogue reconnu à lire.")
            return
        try:
            voice = self.voice_map[self.store.voice_for(dialogue.npc, list(self.voice_map))]
            utterances = [Utterance(dialogue.text, voice)]
            if self.read_responses.isChecked() and dialogue.responses:
                response_voice = self.voice_map.get(self.response_combo.currentData(), voice)
                if response_voice.key == voice.key and len(self.voices) > 1:
                    response_voice = next(v for v in self.voices if v.key != voice.key)
                    self.response_combo.setCurrentIndex(self.response_combo.findData(response_voice.key))
                utterances.extend(Utterance(text, response_voice) for text in dialogue.responses if text.strip())
            self.playback.submit(utterances, self.speed.value())
        except (ValueError, KeyError) as exc:
            self.show_error(str(exc))

    def replay(self):
        self.speak_dialogue()

    def audio_started(self, epoch, elapsed):
        if epoch == self.playback.epoch:
            self.metrics.setText(self.metrics.text().split(" • synthèse")[0] + f" • synthèse → lancement audio : {elapsed * 1000:.0f} ms")

    def assign_voice(self, _):
        if self.last_dialogue and self.voice_combo.currentData():
            self.playback.cancel()
            self.store.assign(self.last_dialogue.npc, self.voice_combo.currentData())

    def preview_voice(self):
        voice = self.voice_map.get(self.voice_combo.currentData())
        if voice:
            self.playback.submit([Utterance("Bonjour, je vous accompagne dans le Monde des Douze. Écoutez ma voix avant de me choisir.", voice)], self.speed.value())

    def edit_regions(self):
        if self.monitor:
            self.stop()
        if self.source is None:
            self.show_error("Choisissez d’abord une source.")
            return
        self.background(self.source, self.events.snapshot.emit)

    def edit_snapshot(self, image):
        dialog = RegionDialog(image, self.last_regions, self)
        if dialog.exec():
            regions = dialog.preview.regions
            self.store.settings.regions[self.source_key] = {key: [r.x, r.y, r.width, r.height] for key, r in regions.items()}
            self.store.save()
            self.auto_regions.blockSignals(True)
            self.auto_regions.setChecked(False)
            self.auto_regions.blockSignals(False)
            self.start()

    def permissions(self):
        QMessageBox.information(self, "Autorisation macOS", "Dans Réglages Système → Confidentialité et sécurité → Enregistrement de l’écran et de l’audio système, autorisez l’application indiquée par macOS (Terminal/Python ou le composant natif).\n\n"
            "Relancez Voix des Douze après modification. Le mode démonstration et l’ouverture d’images fonctionnent sans capture d’écran. Aucun accès au micro n’est utilisé.")
        self.background(macos.request_permission, lambda _: self.events.error.emit("Demande transmise à macOS. Vérifiez les Réglages Système puis relancez si nécessaire."))

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
        layout.addRow(QLabel("Touches F1–F12 : Fn peut être nécessaire selon votre clavier.\nAucune frappe de jeu n’est observée ou automatisée."))
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
