"""Deux workers bornés : synthèse anticipée et lecture afplay interruptible."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave

from .speech import SpeechEngine, VoiceChoice

from .activity import UserActivity

@dataclass(frozen=True)
class Utterance:
    text: str
    voice: VoiceChoice


class Playback:
    def __init__(self, engine: SpeechEngine, status=lambda text: None, started=lambda epoch, delay: None,
                 warmup=(), ready=lambda: None):
        self.engine = engine
        self.status = status
        self.started = started
        self.warmup_choices = warmup
        self.ready_callback = ready
        self.condition = threading.Condition(threading.RLock())
        self.epoch = 0
        self.pending = None
        self.audio = deque()
        self.process = None
        self.closed = False
        self.volume = 0.8
        self.threads = [threading.Thread(target=self._generate, daemon=True, name="Synthèse"),
                        threading.Thread(target=self._play, daemon=True, name="Audio")]
        for thread in self.threads:
            thread.start()

    def cancel(self) -> int:
        with self.condition:
            self.epoch += 1
            self.pending = None
            self.audio.clear()
            abort = getattr(self.engine, "abort", None)
            if abort is not None:
                abort()
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass  # It exited between poll() and kill().
            self.condition.notify_all()
            return self.epoch

    def submit(self, utterances: list[Utterance], speed=1.0) -> int:
        with self.condition:
            epoch = self.cancel()
            self.pending = (epoch, utterances, speed, time.perf_counter())
            self.condition.notify_all()
            return epoch

    def _generate(self):
        try:
            if self.warmup_choices:
                with UserActivity():
                    self.engine.warmup(self.warmup_choices)
            self.ready_callback()
        except Exception as exc:
            self.status(f"Préchauffage impossible : {exc}")
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                epoch, utterances, speed, submitted = self.pending
                self.pending = None
            try:
                with UserActivity():
                    for utterance in utterances:
                        with self.condition:
                            if epoch != self.epoch or self.closed:
                                break
                            chunks = self.engine.synthesize(utterance.text, utterance.voice, speed)
                        try:
                            for chunk in chunks:
                                with self.condition:
                                    self.condition.wait_for(lambda: self.closed or epoch != self.epoch or len(self.audio) < 2)
                                    if self.closed or epoch != self.epoch:
                                        break
                                    self.audio.append((epoch, chunk, submitted))
                                    self.condition.notify_all()
                        finally:
                            close = getattr(chunks, "close", None)
                            if close is not None:
                                close()
            except Exception as exc:
                with self.condition:
                    if epoch == self.epoch:
                        self.status(str(exc))

    def _play(self):
        first_epoch = None
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or bool(self.audio))
                if self.closed:
                    return
                epoch, chunk, submitted = self.audio.popleft()
                self.condition.notify_all()
            # A private file remains alive until afplay completes; no output can race cancellation.
            with tempfile.TemporaryDirectory(prefix="voix-audio-") as folder:
                path = Path(folder) / "segment.wav"
                with wave.open(str(path), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(chunk.sample_rate)
                    stream.writeframes(chunk.pcm)
                with self.condition:
                    if epoch != self.epoch or self.closed:
                        continue
                    self.process = subprocess.Popen(["/usr/bin/afplay", "-v", str(self.volume), str(path)],
                                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    process = self.process
                    if first_epoch != epoch:
                        first_epoch = epoch
                        self.started(epoch, time.perf_counter() - submitted)
                _, stderr = process.communicate()
                with self.condition:
                    if epoch == self.epoch and process.returncode:
                        self.status(f"Lecture audio impossible : {stderr.decode(errors='replace').strip()}")
                    if self.process is process:
                        self.process = None

    def close(self):
        with self.condition:
            self.closed = True
            self.cancel()
        close = getattr(self.engine, "close", None)
        if close is not None:
            close()
        for thread in self.threads:
            thread.join(timeout=5)
