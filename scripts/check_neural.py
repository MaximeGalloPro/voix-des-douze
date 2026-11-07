#!/usr/bin/env python3
"""Synthèse réelle offline, WAV et mesures (sans lecture haut-parleur).

.venv-neural/bin/python scripts/check_neural.py --engine sopro|pocket
Rapport et WAV hors Git par défaut dans .context/neural-ENGINE/.
Garde sockets Python, pas un pare-feu système. Aucune écoute humaine garantie.
"""
from __future__ import annotations

import argparse
from array import array
from contextlib import ExitStack
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import socket
import sys
import time
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voix_des_douze.neural_speech import NeuralSpeechEngine, available_voices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("sopro", "pocket"))
    parser.add_argument("--model-dir", type=Path, default=Path.home() / "Library/Caches/VoixDesDouze/models")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or ROOT / ".context" / f"neural-{args.engine}"
    output.mkdir(parents=True, exist_ok=True)
    text = "Bonjour aventurier. Herdegrize vous attend près du château !"
    report = {"engine": args.engine, "text": text, "platform": platform.platform(),
              "python": platform.python_version(), "packages": {name: importlib.metadata.version(name)
              for name in ("sopro", "pocket-tts", "torch", "torchaudio")},
              "offline": "socket.connect/connect_ex/create_connection/getaddrinfo interdits, imports inclus",
              "limits": ["Garde Python, pas pare-feu système", "Pas d'écoute humaine", "Livraison PCM, pas latence haut-parleur",
                         "Une phrase complète avant livraison", "Vitesse par rééchantillonnage, hauteur modifiée"], "voices": []}

    def deny(*args, **kwargs):
        raise RuntimeError("Socket interdit pendant la synthèse")

    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"):
            stack.enter_context(patch(target, deny))
        try:
            socket.create_connection(("example.org", 443))
        except RuntimeError:
            report["socket_guard_checked"] = True
        else:
            raise AssertionError("Garde socket inactif")
        voices = available_voices(args.model_dir, args.engine)
        if len(voices) != 4:
            raise RuntimeError("Préparer quatre références synthétiques avec scripts/prepare_neural.py")
        engine = NeuralSpeechEngine(args.model_dir, args.engine)
        for voice in voices:
            runs = []
            for index in range(2):
                start = time.perf_counter()
                chunks = engine.synthesize(text, voice)
                first = next(chunks)
                first_s = time.perf_counter() - start
                segments = [first, *chunks]
                elapsed = time.perf_counter() - start
                pcm = b"".join(segment.pcm for segment in segments)
                signal = array("h", pcm)
                duration = len(signal) / first.sample_rate
                rms = math.sqrt(sum(x * x for x in signal) / len(signal))
                assert 1 < duration < 30 and rms > 100
                runs.append({"first_segment_s": first_s, "total_s": elapsed, "duration_s": duration,
                             "rtf": elapsed / duration, "segments": len(segments), "rms_int16": rms,
                             "peak_int16": max(abs(x) for x in signal), "pcm_sha256": hashlib.sha256(pcm).hexdigest()})
                if index == 0:
                    filename = voice.key.replace(":", "-") + ".wav"
                    with wave.open(str(output / filename), "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(first.sample_rate)
                        wav.writeframes(pcm)
            report["voices"].append({"key": voice.key, "sample_rate": first.sample_rate,
                                     "file": filename, "first": runs[0], "warm": runs[1]})
            print(json.dumps(report["voices"][-1], ensure_ascii=False), flush=True)
    destination = output / "results.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Mesures : {destination}")


if __name__ == "__main__":
    main()
