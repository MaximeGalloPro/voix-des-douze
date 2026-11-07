"""Protocole JSON local ; stdout est réservé aux segments PCM, aucun réseau."""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import json
from pathlib import Path
import socket
import sys

from .speech import SpeechEngine, VoiceChoice


def main():
    engine_id, directory = sys.argv[1:]
    def offline(*args, **kwargs):
        raise RuntimeError("Connexion interdite pendant la synthèse hors ligne.")
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.create_connection = offline
    socket.getaddrinfo = offline
    output = sys.stdout
    with redirect_stdout(sys.stderr):
        if engine_id == "piper":
            engine = SpeechEngine(Path(directory))
        else:
            from .neural_speech import NeuralSpeechEngine
            engine = NeuralSpeechEngine(Path(directory), engine_id)
    def send(message):
        output.write(json.dumps(message) + "\n")
        output.flush()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                choices = [VoiceChoice(**item) for item in request["voices"]]
                if request["action"] == "warmup":
                    engine.warmup(choices)
                else:
                    for segment in engine.synthesize(request["text"], choices[0], request["speed"]):
                        send({"pcm": base64.b64encode(segment.pcm).decode("ascii"),
                              "sample_rate": segment.sample_rate})
            send({"done": True})
        except Exception as exc:
            send({"error": f"{type(exc).__name__} : {exc}"})


if __name__ == "__main__":
    main()
