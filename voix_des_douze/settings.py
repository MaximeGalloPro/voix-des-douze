"""Configuration locale atomique ; aucune communication réseau."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
import threading
import unicodedata


def character_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def data_directory() -> Path:
    return Path.home() / "Library/Application Support/VoixDesDouze"


def model_directory() -> Path:
    return Path.home() / "Library/Caches/VoixDesDouze/models"


@dataclass
class Settings:
    automatic: bool = True
    read_responses: bool = False
    volume: float = 0.8
    speed: float = 1.0
    default_voice: str = ""
    response_voice: str = ""
    assignments: dict[str, str] = field(default_factory=dict)
    regions: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    shortcuts: dict[str, str] = field(default_factory=lambda: {
        "toggle": "F6", "stop": "F7", "replay": "F8"})


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / "settings.json"
        self.lock = threading.RLock()
        self.settings = Settings()
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.settings = Settings(**raw)
                s = self.settings
                if not (0 <= s.volume <= 1 and 0.5 <= s.speed <= 2):
                    raise ValueError("Volume ou vitesse hors limites")
                if not all(isinstance(v, dict) for v in (s.assignments, s.regions, s.shortcuts)):
                    raise ValueError("Configuration invalide")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Configuration illisible : {self.path}. Conservez ce fichier avant de le corriger. {exc}") from exc

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix="settings-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(asdict(self.settings), stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally:
                Path(temp).unlink(missing_ok=True)

    def voice_for(self, name: str, choices: list[str]) -> str:
        if not choices:
            raise ValueError("Aucune voix préparée. Lancez Installer.command.")
        with self.lock:
            fallback = self.settings.default_voice
            if fallback not in choices:
                fallback = choices[0]
            key = character_key(name)
            if not key:
                return fallback
            assigned = self.settings.assignments.get(key)
            if assigned not in choices:
                used = list(self.settings.assignments.values())
                assigned = min(choices, key=lambda v: used.count(v))
                self.settings.assignments[key] = assigned
                self.save()
            return assigned

    def assign(self, name: str, voice: str):
        with self.lock:
            if character_key(name):
                self.settings.assignments[character_key(name)] = voice
                self.save()
