#!/usr/bin/env python3
"""Lecture réelle des WAV complets via afplay ; ne prouve pas l'audition humaine.

Chaque processus doit sortir avec code 0 après au moins la durée du signal
(marge de 0,2 s). Aucun enregistrement de la sortie matérielle n'est effectué.
"""
from pathlib import Path
import argparse
from array import array
import json
import subprocess
import time
import wave

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume", type=float, default=0.15)
    parser.add_argument("--output", type=Path, default=ROOT / "samples/audio-completion.json")
    args = parser.parse_args()
    if not 0 <= args.volume <= 1:
        parser.error("Volume attendu entre 0 et 1")
    results = []
    for path in sorted((ROOT / "samples").glob("*.wav")):
        with wave.open(str(path), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
            assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
            signal = array("h", wav.readframes(wav.getnframes()))
            assert max(signal) - min(signal) > 1000
        start = time.perf_counter()
        process = subprocess.run(["/usr/bin/afplay", "-v", str(args.volume), str(path)],
                                 capture_output=True, text=True, timeout=duration + 15)
        elapsed = time.perf_counter() - start
        result = {"file": path.name, "wav_duration_s": duration, "afplay_elapsed_s": elapsed,
                  "exit_code": process.returncode, "stderr": process.stderr,
                  "played_through_process": process.returncode == 0 and elapsed >= duration - .2}
        results.append(result)
        if not result["played_through_process"]:
            raise RuntimeError(f"Lecture complète non confirmée : {result}")
    if not results:
        raise RuntimeError("Aucun échantillon WAV disponible")
    report = {"command": "scripts/check_audio.py --volume " + str(args.volume),
              "human_listening": False, "hardware_loopback_recording": False,
              "limit": "Fin normale des processus et durée cohérente, sans preuve du son effectivement audible.",
              "files": results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
