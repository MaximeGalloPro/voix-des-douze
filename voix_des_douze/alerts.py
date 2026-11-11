"""Short local cue, independent from speech generation and dialogue cancellation."""
import math
from pathlib import Path
import struct
import tempfile
import wave

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QSoundEffect


class ResourceAlert(QObject):
    def __init__(self, parent=None, error=lambda message: None):
        super().__init__(parent)
        self.directory = tempfile.TemporaryDirectory(prefix="voix-resource-alert-")
        path = Path(self.directory.name) / "resource.wav"
        sample_rate = 22050
        samples = bytearray()
        duration = 0.14
        count = int(sample_rate * duration)
        for index in range(count):
            time = index / sample_rate
            # A low descending pulse, softly faded at both ends: no sharp click.
            envelope = (1 - math.exp(-time / 0.003)) * math.exp(-time / 0.028)
            envelope *= min(1, (count - 1 - index) / (sample_rate * 0.01))
            phase = 2 * math.pi * (180 * time - 80 * time * time / (2 * duration))
            value = int(10000 * envelope * math.sin(phase))
            samples.extend(struct.pack("<h", value))
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(samples)
        self.effect = QSoundEffect(self)
        self.error = error
        self.pending = False
        self.effect.statusChanged.connect(self._status)
        self.effect.setSource(QUrl.fromLocalFile(str(path)))

    def _status(self):
        if self.effect.status() == QSoundEffect.Error:
            self.pending = False
            self.error("Alerte ressources : lecture du son impossible.")
        elif self.effect.status() == QSoundEffect.Ready and self.pending:
            self.pending = False
            self.effect.play()

    def play(self, volume):
        self.effect.setVolume(volume)
        if self.effect.status() == QSoundEffect.Error:
            self.error("Alerte ressources : lecture du son impossible.")
        elif self.effect.status() == QSoundEffect.Ready:
            self.effect.play()
        else:
            self.pending = True

    def stop(self):
        self.pending = False
        self.effect.stop()

    def close(self):
        self.stop()
        self.effect.setSource(QUrl())
        self.directory.cleanup()
