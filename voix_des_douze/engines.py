"""Un seul processus vocal résident, interrompu réellement à l'annulation."""
from __future__ import annotations

import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from .speech import AudioSegment, available_voices as piper_voices

ENGINES = {"piper": "Piper", "sopro": "Sopro V2 Turbo", "pocket": "Pocket TTS français"}
ROOT = Path(__file__).resolve().parents[1]


def available_voices(directory, engine):
    if engine not in ENGINES:
        raise ValueError("Moteur vocal inconnu.")
    if engine == "piper":
        return piper_voices(directory)
    from .neural_speech import available_voices as neural_voices
    return neural_voices(Path(directory), engine)


class ManagedSpeechEngine:
    def __init__(self, directory, engine):
        if engine not in ENGINES:
            raise ValueError("Moteur vocal inconnu.")
        self.directory, self.engine = Path(directory), engine
        self._state = threading.Lock()
        self._io = threading.Lock()
        self._process = None
        self._busy = False
        self._closed = False
        self._log = None
        self._interruptible = False
        self._revision = 0

    def _start(self):
        python = Path(sys.executable) if self.engine == "piper" else ROOT / ".venv-neural/bin/python"
        if not python.is_file():
            raise RuntimeError("Dépendances vocales absentes. Utilisez « Télécharger / préparer ».")
        environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           HF_DATASETS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        self._log = tempfile.TemporaryFile()
        try:
            self._process = subprocess.Popen(
                [str(python), "-m", "voix_des_douze.speech_worker", self.engine, str(self.directory)],
                cwd=ROOT, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log, text=True, bufsize=1)
        except Exception:
            self._log.close()
            self._log = None
            raise

    def _request(self, request, revision=None):
        with self._io:
            with self._state:
                if self._closed:
                    raise RuntimeError("Moteur vocal fermé.")
                if revision is not None and revision != self._revision:
                    raise RuntimeError("Synthèse annulée.")
                if self._process is None:
                    self._start()
                process, log = self._process, self._log
                self._busy = True
                self._interruptible = request["action"] == "synthesize"
            completed = False
            try:
                process.stdin.write(json.dumps(request) + "\n")
                process.stdin.flush()
                while True:
                    line = process.stdout.readline()
                    with self._state:
                        if self._process is not process:
                            raise RuntimeError("Synthèse annulée.")
                    if not line:
                        log.seek(0)
                        details = log.read().decode(errors="replace")[-2000:]
                        raise RuntimeError("Processus vocal interrompu. " + details)
                    message = json.loads(line)
                    if "error" in message:
                        completed = True
                        raise RuntimeError(message["error"])
                    if message.get("done"):
                        completed = True
                        return
                    yield AudioSegment(base64.b64decode(message["pcm"], validate=True), message["sample_rate"])
            finally:
                with self._state:
                    self._busy = False
                    if not completed and self._process is process:
                        self._process, self._log = None, None
                    detached = self._process is not process
                if detached:
                    self._dispose(process, log)

    @staticmethod
    def _dispose(process, log):
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()
        log.close()

    def abort(self):
        """Couper un calcul actif, conserver le modèle lorsqu'il est au repos."""
        with self._state:
            self._revision += 1
            if self._busy and self._interruptible and self._process is not None:
                process = self._process
                self._process, self._log = None, None
                if process.poll() is None:
                    process.kill()

    def close(self):
        with self._state:
            self._closed = True
            process, log, busy = self._process, self._log, self._busy
            self._process, self._log = None, None
            if process is not None and process.poll() is None:
                process.kill()
        if process is not None and not busy:
            self._dispose(process, log)

    def warmup(self, choices):
        for _ in self._request({"action": "warmup", "voices": [asdict(v) for v in choices]}):
            pass

    def synthesize(self, text, choice, speed=1.0):
        # Capturer le bail dès l'appel, avant la première itération paresseuse.
        with self._state:
            revision = self._revision
        return self._request({"action": "synthesize", "text": text,
                              "voices": [asdict(choice)], "speed": speed}, revision)
