"""Configuration locale atomique ; aucune communication réseau."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
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
    resource_sound: bool = True
    first_resource_click: bool = False
    idle_shortcut_enabled: bool = True
    compass_enabled: bool = False
    compass_profile: str = ""
    compass_profiles: list[str] = field(default_factory=list)
    compass_positions: dict[str, list[int]] = field(default_factory=dict)
    volume: float = 0.8
    speed: float = 1.0
    default_voice: str = ""
    assignments: dict[str, str] = field(default_factory=dict)
    engine: str = "piper"
    engine_defaults: dict[str, str] = field(default_factory=dict)
    engine_assignments: dict[str, dict[str, str]] = field(default_factory=dict)
    shortcuts: dict[str, str] = field(default_factory=lambda: {
        "toggle": "F6", "stop": "F7", "replay": "F8", "idle": "="})
    overlay_screen: str = ""
    overlay_adjustments: dict[str, list[float]] = field(default_factory=dict)


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / "settings.json"
        self.lock = threading.RLock()
        self.settings = Settings()
        if self.path.exists():
            try:
                original = self.path.read_text(encoding="utf-8")
                raw = json.loads(original)
                # One-time settings migration; voice choices and shortcuts stay intact.
                retired = {"regions", "read_responses", "response_voice"}
                migrated = bool(retired.intersection(raw))
                self.settings = Settings(**{key: value for key, value in raw.items() if key not in retired})
                s = self.settings
                if not (0 <= s.volume <= 1 and 0.5 <= s.speed <= 2):
                    raise ValueError("Volume ou vitesse hors limites")
                if not all(isinstance(v, dict) for v in (s.assignments, s.shortcuts)):
                    raise ValueError("Configuration invalide")
                if s.shortcuts.get("idle", "F9") == "F9":
                    s.shortcuts["idle"] = "="
                    migrated = True
                if s.engine not in ("piper", "sopro", "pocket"):
                    raise ValueError("Moteur vocal inconnu")
                if not isinstance(s.engine_defaults, dict) or not isinstance(s.engine_assignments, dict):
                    raise ValueError("Configuration des moteurs invalide")
                if not all(isinstance(v, dict) for v in s.engine_assignments.values()):
                    raise ValueError("Attributions des moteurs invalides")
                if not isinstance(s.overlay_screen, str) or not isinstance(s.overlay_adjustments, dict):
                    raise ValueError("Réglage des repères invalide")
                if not isinstance(s.compass_enabled, bool) or not isinstance(s.compass_profile, str):
                    raise ValueError("Configuration de la boussole invalide")
                if (not isinstance(s.compass_profiles, list)
                        or not all(isinstance(p, str) and p.strip() for p in s.compass_profiles)
                        or not isinstance(s.compass_positions, dict)
                        or not all(isinstance(key, str) and isinstance(pos, list) and len(pos) == 2
                                   and all(type(v) is int for v in pos)
                                   for key, pos in s.compass_positions.items())):
                    raise ValueError("Profils ou positions de boussole invalides")
                for adjustment in s.overlay_adjustments.values():
                    if (not isinstance(adjustment, list) or len(adjustment) != 3
                            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in adjustment)
                            or not 0.2 <= adjustment[2] <= 3):
                        raise ValueError("Décalage ou échelle des repères invalide")
                if migrated:
                    backup = self.path.with_name(self.path.stem + "-before-network.json")
                    if not backup.exists():
                        with backup.open("x", encoding="utf-8") as stream:
                            stream.write(original)
                    self.save()
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

    def voice_for(self, name: str, choices: list[str], engine: str = "piper") -> str:
        if not choices:
            raise ValueError("Aucune voix préparée. Lancez Installer.command.")
        with self.lock:
            fallback = self.default_for(engine)
            if fallback not in choices:
                fallback = choices[0]
            key = character_key(name)
            if not key:
                return fallback
            assignments = self.settings.assignments if engine == "piper" else self.settings.engine_assignments.setdefault(engine, {})
            assigned = assignments.get(key)
            if assigned not in choices:
                used = list(assignments.values())
                assigned = min(choices, key=lambda v: used.count(v))
                assignments[key] = assigned
                self.save()
            return assigned

    def default_for(self, engine: str) -> str:
        return self.settings.default_voice if engine == "piper" else self.settings.engine_defaults.get(engine, "")

    def set_default(self, engine: str, voice: str):
        if engine == "piper":
            self.settings.default_voice = voice
        else:
            self.settings.engine_defaults[engine] = voice

    def assign(self, name: str, voice: str, engine: str = "piper"):
        with self.lock:
            if character_key(name):
                assignments = self.settings.assignments if engine == "piper" else self.settings.engine_assignments.setdefault(engine, {})
                assignments[character_key(name)] = voice
                self.save()
