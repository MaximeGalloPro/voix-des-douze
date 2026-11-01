#!/usr/bin/env python3
"""Mesures réelles sur les trois captures recadrées, sans client Dofus en direct.

--play lance réellement afplay sur le premier segment de chaque mesure, puis
l'interrompt après 0,1 s. Le timestamp Popen n'est PAS une mesure acoustique.
"""
from pathlib import Path
import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from voix_des_douze.macos import permission_status
from voix_des_douze.settings import Store, model_directory
from voix_des_douze.speech import SpeechEngine, available_voices
from voix_des_douze.vision import detect_regions, recognize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "samples/pipeline-results.json")
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()
    report = {"platform": platform.platform(), "python": platform.python_version(),
        "source": "Recadrages réels fournis par l'utilisateur, replacés sur un fond neutre 2048×1280.",
        "limits": ["Pas de capture de fenêtre en direct.", "Pas d'écoute humaine.",
            "Froid : premier appel du processus, binaire Swift déjà compilé, cache disque non vidé.",
            "Afplay : horodatage du lancement du processus, pas du premier son matériel.",
            "Délai de stabilité de 350 ms et cadence de capture exclus du benchmark direct."], "cases": []}
    start = time.perf_counter()
    report["screen_permission"] = permission_status()
    report["native_start_s"] = time.perf_counter() - start
    engine = SpeechEngine(model_directory())
    voices = available_voices(model_directory())
    if len(voices) < 4:
        raise RuntimeError("Les quatre voix préparées sont nécessaires.")
    start = time.perf_counter()
    engine.warmup(voices)
    report["piper_warmup_s"] = time.perf_counter() - start
    with tempfile.TemporaryDirectory(prefix="voix-benchmark-") as tmp:
        store = Store(Path(tmp) / "settings.json")
        for name, origin in [("kerubim", (1290, 370)), ("nacf", (750, 480)), ("al", (1290, 330))]:
            image = Image.new("RGB", (2048, 1280))
            with Image.open(ROOT / "tests/fixtures/dialogues" / (name + ".png")) as crop:
                image.paste(crop, origin)
                image.info.update(crop.info)
            case = {"fixture": name, "runs": []}
            for run in range(4):
                start = time.perf_counter()
                regions = detect_regions(image)
                detection_s = time.perf_counter() - start
                ocr_start = time.perf_counter()
                dialogue = recognize(image, regions)
                ocr_s = time.perf_counter() - ocr_start
                choice = store.voice_for(dialogue.npc, [v.key for v in voices])
                voice = next(v for v in voices if v.key == choice)
                speech_start = time.perf_counter()
                generator = engine.synthesize(dialogue.text, voice)
                first = next(generator)
                first_s = time.perf_counter() - speech_start
                pcm_ready_s = time.perf_counter() - start
                segment = Path(tmp) / "segment.wav"
                with wave.open(str(segment), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(first.sample_rate)
                    wav.writeframes(first.pcm)
                launch_s = None
                if args.play:
                    process = subprocess.Popen(["/usr/bin/afplay", "-v", "0.15", str(segment)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    launch_s = time.perf_counter() - start
                    time.sleep(.1)
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=5)
                rest = list(generator)
                speech_total_s = time.perf_counter() - speech_start
                case.update(npc=dialogue.npc, voice=voice.key, responses=list(dialogue.responses))
                case["runs"].append({"first_vision_call": len(report["cases"]) == 0 and run == 0,
                    "detection_s": detection_s, "ocr_s": ocr_s, "speech_first_pcm_s": first_s,
                    "image_to_first_pcm_s": pcm_ready_s, "image_to_afplay_launch_s": launch_s,
                    "speech_total_including_play_probe_s": speech_total_s,
                    "audio_duration_s": sum(len(s.pcm) / (2 * s.sample_rate) for s in [first, *rest])})
            report["cases"].append(case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
