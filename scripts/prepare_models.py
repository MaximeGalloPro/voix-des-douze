#!/usr/bin/env python3
"""Téléchargement EXPLICITE des voix françaises figées dans models/manifest.json.

Usage : python scripts/prepare_models.py [--model-dir CHEMIN] [--verify-only]
Mesures hors réseau : python scripts/prepare_models.py --verify-only --samples-dir samples
Reprise .part par Range si le serveur l'accepte, remplacement atomique après SHA256.
Les fichiers déjà vérifiés ne sont pas téléchargés. Une divergence est une erreur,
pas une acceptation silencieuse. Les MODEL_CARD sont conservées pour attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

DEFAULT_MODEL_DIR = Path.home() / "Library/Caches/VoixDesDouze/models"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "models/manifest.json"


def verified(path: Path, item: dict) -> bool:
    if not path.is_file() or path.stat().st_size != item["size"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == item["sha256"]


def download(url: str, path: Path, item: dict) -> None:
    if verified(path, item):
        print(f"Vérifié : {path.name}")
        return
    if path.exists():
        raise RuntimeError(f"Fichier existant non conforme, laissé intact : {path}. Déplacez-le avant de relancer.")
    partial = path.with_name(path.name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset and verified(partial, item):
        partial.replace(path)
        return
    if offset >= item["size"]:
        partial.unlink()
        offset = 0
    request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
    with urllib.request.urlopen(request, timeout=120) as response:
        resumed = offset and response.status == 206
        if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise RuntimeError(f"Reprise HTTP invalide : {path.name}")
        with partial.open("ab" if resumed else "wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
    if not verified(partial, item):
        raise RuntimeError(f"Taille ou SHA256 incorrect : {partial}. Relancez pour reprendre.")
    partial.replace(path)
    print(f"Installé : {path.name}")


def prepare(model_dir: Path, *, verify_only: bool = False) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    model_dir.mkdir(parents=True, exist_ok=True)
    base_url = f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['revision']}"
    for model in manifest["models"]:
        name = model["name"]
        for item in model["files"]:
            filename = name + "-MODEL_CARD" if item["source"] == "MODEL_CARD" else item["source"]
            path = model_dir / filename
            if verify_only:
                if not verified(path, item):
                    raise RuntimeError(f"Fichier absent ou SHA256 incorrect : {path}")
            else:
                download(f"{base_url}/{model['source_dir']}/{item['source']}", path, item)
        config = json.loads((model_dir / (name + ".onnx.json")).read_text(encoding="utf-8"))
        card = (model_dir / (name + "-MODEL_CARD")).read_text(encoding="utf-8")
        if f"License: {model['license']}" not in card or model["dataset_url"] not in card:
            raise RuntimeError(f"Licence/provenance incohérente : {name}")
        if (config["num_speakers"] != model["num_speakers"]
                or config["audio"]["sample_rate"] != model["sample_rate"]
                or config["language"]["code"] != "fr_FR"
                or config["espeak"]["voice"] != "fr"):
            raise RuntimeError(f"Configuration incohérente : {name}")
        for corpus_id, speaker_id in model["selected_speakers"].items():
            if not 0 <= speaker_id < config["num_speakers"]:
                raise RuntimeError(f"Locuteur hors limites : {name}/{speaker_id}")
            if config["num_speakers"] > 1 and config["speaker_id_map"].get(corpus_id) != speaker_id:
                raise RuntimeError(f"Locuteur incohérent : {name}/{corpus_id}")
        print(f"{name} : {model['license']}, {model['num_speakers']} locuteur(s), {model['sample_rate']} Hz")


def measure(model_dir: Path, output_dir: Path) -> None:
    """Produire WAV et mesures réelles, sockets Python interdits pendant l'inférence.

    --verify-only --samples-dir samples permet de reproduire sans réseau.
    « Froid » = nouvelle session ONNX, pas un cache disque macOS vidé.
    Les mesures s'arrêtent à la livraison PCM, pas au démarrage d'un haut-parleur.
    """
    import importlib.metadata
    import platform
    import socket
    import sys
    import time
    import wave
    from array import array
    from contextlib import ExitStack
    from unittest.mock import patch

    sys.path.insert(0, str(MANIFEST_PATH.parent.parent))
    from voix_des_douze.speech import SpeechEngine, available_voices

    output_dir.mkdir(parents=True, exist_ok=True)
    text = "Bonjour aventurier. Herdegrize vous attend près du château !"
    report = {
        "text": text,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "piper_tts": importlib.metadata.version("piper-tts"),
        "onnxruntime": importlib.metadata.version("onnxruntime"),
        "model_revision": json.loads(MANIFEST_PATH.read_text())["revision"],
        "cpu_threads": {"intra": 2, "inter": 1},
        "offline_proof": "Synthèse réelle, imports compris, socket.connect/connect_ex/create_connection/getaddrinfo interdits par exception Python.",
        "limits": [
            "Pas d'écoute humaine ni de classement de qualité perceptive.",
            "Froid = nouvelle session ONNX ; cache disque non vidé.",
            "Latence livraison PCM ; aucun démarrage audio matériel mesuré.",
            "Interdiction sockets Python, pas un pare-feu système.",
        ],
        "voices": [],
    }

    def deny(*args, **kwargs):
        raise RuntimeError("Accès socket interdit pendant les mesures")

    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "socket.getaddrinfo"):
            stack.enter_context(patch(target, deny))
        # Vérifier que le garde réseau est bien actif.
        try:
            socket.create_connection(("example.org", 443))
        except RuntimeError:
            report["socket_guard_checked"] = True
        else:
            raise AssertionError("Garde socket inactif")
        pcm_hashes = []
        for choice in available_voices(model_dir):
            engine = SpeechEngine(model_dir)
            runs = []
            for run in range(4):
                start = time.perf_counter()
                chunks = engine.synthesize(text, choice)
                first = next(chunks)
                first_s = time.perf_counter() - start
                segments = [first, *chunks]
                elapsed = time.perf_counter() - start
                pcm = b"".join(segment.pcm for segment in segments)
                duration = len(pcm) / (2 * first.sample_rate)
                runs.append({"first_chunk_s": first_s, "total_s": elapsed,
                             "duration_s": duration, "rtf": elapsed / duration,
                             "segments": len(segments)})
                if run == 0:
                    filename = choice.key.replace(":", "-") + ".wav"
                    path = output_dir / filename
                    with wave.open(str(path), "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(first.sample_rate)
                        wav.writeframes(pcm)
                    with wave.open(str(path), "rb") as wav:
                        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
                        assert wav.getframerate() == first.sample_rate
                        samples = array("h", wav.readframes(wav.getnframes()))
                    rms = (sum(value * value for value in samples) / len(samples)) ** 0.5
                    assert 1 < duration < 30 and rms > 100
                    digest = hashlib.sha256(pcm).hexdigest()
                    pcm_hashes.append(digest)
                    audio = {"file": filename, "sample_rate": first.sample_rate,
                             "channels": 1, "sample_width": 2,
                             "pcm_sha256": digest, "rms_int16": rms,
                             "peak_int16": max(abs(value) for value in samples)}
            report["voices"].append({
                "key": choice.key, "label": choice.label, "model": choice.model,
                "speaker_id": choice.speaker_id, "audio": audio,
                "cold": runs[0], "warm": runs[1:],
            })
        if len(pcm_hashes) != 4 or len(set(pcm_hashes)) != 4:
            raise RuntimeError("Quatre voix distinctes installées sont requises pour ces mesures.")
        report["four_distinct_pcm"] = True
    (output_dir / "speech-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"WAV et mesures : {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--verify-only", action="store_true", help="Vérifier sans aucune connexion réseau")
    parser.add_argument("--samples-dir", type=Path, help="Produire quatre WAV et speech-results.json, inférence hors réseau")
    args = parser.parse_args()
    try:
        prepare(args.model_dir.expanduser(), verify_only=args.verify_only)
        if args.samples_dir is not None:
            measure(args.model_dir.expanduser(), args.samples_dir)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Préparation impossible : {error}\n")


if __name__ == "__main__":
    main()
