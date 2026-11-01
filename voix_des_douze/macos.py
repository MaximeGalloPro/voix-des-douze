"""Capture ScreenCaptureKit macOS 14+, pixels Retina, fenêtre indépendante du bureau.

Le helper Swift reste chargé pour amortir Vision. Compilation locale au premier appel
(Command Line Tools requis), binaire privé dans ~/Library/Caches/VoixDesDouze.
Aucun appel ne demande implicitement une permission : utiliser request_permission().
Les appels bloquent : les exécuter dans le worker de l’application, jamais son UI.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import select
import subprocess
import tempfile
import threading

from PIL import Image


class CaptureError(RuntimeError):
    """Erreur native, compilation indisponible ou capture impossible."""


class CapturePermissionError(CaptureError):
    """Autorisation macOS absente ; aucune tentative de contournement."""


class WindowClosedError(CaptureError):
    """La fenêtre sélectionnée n’est plus partageable."""


@dataclass(frozen=True)
class Window:
    window_id: int
    title: str
    app: str
    width: int
    height: int


class _Native:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()

    def _start(self):
        if platform.system() != "Darwin":
            raise CaptureError("Ce moteur nécessite macOS 14 ou plus récent.")
        if int(platform.mac_ver()[0].split('.')[0]) < 14:
            raise CaptureError("ScreenCaptureKit nécessite ici macOS 14 ou plus récent.")
        source = Path(__file__).parent / "native" / "Engine.swift"
        digest = hashlib.sha256(source.read_bytes() + platform.machine().encode()).hexdigest()[:20]
        cache = Path.home() / "Library" / "Caches" / "VoixDesDouze"
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        binary = cache / f"engine-{digest}"
        if not binary.exists():
            with tempfile.TemporaryDirectory(dir=cache) as directory:
                target = Path(directory) / "engine"
                try:
                    result = subprocess.run(["xcrun", "swiftc", "-O", "-parse-as-library", str(source), "-o", str(target)], capture_output=True, text=True, timeout=120)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise CaptureError("Installez les Command Line Tools Apple (xcode-select --install).") from exc
                if result.returncode:
                    raise CaptureError(f"Compilation du helper Apple impossible : {result.stderr[-4000:]}")
                target.replace(binary)
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def call(self, op, **arguments):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.close()
                self._start()
            try:
                self.process.stdin.write(json.dumps({"op": op, **arguments}) + "\n")
                self.process.stdin.flush()
                if not select.select([self.process.stdout], [], [], 60)[0]:
                    raise CaptureError("Le moteur Apple ne répond pas (60 secondes).")
                line = self.process.stdout.readline()
                if not line:
                    raise CaptureError("Le moteur Apple s’est arrêté.")
                response = json.loads(line)
            except (OSError, ValueError, CaptureError) as exc:
                self.close()
                raise CaptureError(f"Communication avec le moteur Apple impossible : {exc}") from exc
            if "error" in response:
                error = {"permission": CapturePermissionError, "closed": WindowClosedError}.get(response["error"], CaptureError)
                raise error(response["message"])
            return response["result"]


_native = _Native()
atexit.register(_native.close)


def permission_status() -> bool:
    return bool(_native.call("permission"))


def request_permission() -> bool:
    """Ouvre la demande système ; peut nécessiter un redémarrage après accord."""
    return bool(_native.call("request_permission"))


def list_windows() -> list[Window]:
    """Toutes fenêtres partageables, y compris non présentes sur le bureau courant."""
    return [Window(**item) for item in _native.call("windows")]


def capture_window(window_id: int) -> Image.Image:
    """Capture seulement cette fenêtre, sans curseur ni ombre, en RGB natif."""
    if isinstance(window_id, bool) or not isinstance(window_id, int) or not 0 < window_id <= 0xFFFFFFFF:
        raise WindowClosedError("Identifiant de fenêtre invalide.")
    with tempfile.TemporaryDirectory(prefix="voix-capture-") as directory:
        path = Path(directory) / "window.png"
        _native.call("capture", window_id=window_id, path=str(path))
        with Image.open(path) as image:
            return image.convert("RGB")
