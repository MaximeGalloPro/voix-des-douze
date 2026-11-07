"""Parcours Qt réel : sélection, préchauffage, préécoute afplay et déchargement."""
import os
from pathlib import Path
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication

from voix_des_douze.engines import ENGINES, available_voices
from voix_des_douze.settings import Store
from voix_des_douze.ui import MainWindow


def wait_for(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate(), "L’interface n’a pas atteint l’état attendu."


def test_real_ui_switches_three_engines_and_stops_previous_worker(tmp_path):
    directory = Path(os.environ.get("VOIX_MODEL_DIR", Path.home() / "Library/Caches/VoixDesDouze/models"))
    if not all(available_voices(directory, engine) for engine in ENGINES):
        pytest.skip("Préparer les trois moteurs avant le parcours Qt réel.")
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "settings.json")
    store.settings.volume = 0  # afplay réel, pas de son imposé pendant la suite automatique.
    window = MainWindow(store, directory)
    window.show()
    previous = None
    try:
        for engine in ENGINES:
            window.engine_combo.setCurrentIndex(window.engine_combo.findData(engine))
            if previous is not None:
                assert previous.poll() is not None
            wait_for(lambda: window.tts_ready)
            assert window.preview_button.isEnabled()
            assert window.voice_combo.count() == 4
            assert store.settings.engine == engine
            spy = QSignalSpy(window.events.audio)
            QTest.mouseClick(window.preview_button, Qt.LeftButton)
            wait_for(lambda: spy.count() > 0)
            assert "lancement audio" in window.metrics.text()
            previous = window.playback.engine._process
            assert previous.poll() is None
        window.stop()
        wait_for(lambda: window.playback.process is None)
    finally:
        window.close()
        app.processEvents()
    assert previous.poll() is not None
    assert Store(store.path).settings.engine == "pocket"
