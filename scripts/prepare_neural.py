#!/usr/bin/env python3
"""Préparation EXPLICITE Sopro/Pocket français, sans clonage d'enregistrement humain.

python3 scripts/prepare_neural.py --engine sopro|pocket --model-dir CHEMIN
Bootstrap/reexec dans RACINE/.venv-neural ; aucune installation Python globale.
Les références sont exclusivement les quatre WAV Piper synthétiques versionnés.
Pocket requiert l'acceptation de https://huggingface.co/kyutai/pocket-tts et un
jeton HF autorisé configuré par l'utilisateur (ce script ne modifie pas le compte).
--verify-only vérifie l'installation sans téléchargement ni réparation implicite.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_DIR = Path.home() / "Library/Caches/VoixDesDouze/models"
SOPRO_REPO = "samuel-vitorino/sopro-v2-turbo"
SOPRO_REVISION = "c12d0dc7c4a33b3cb6ea3d11ceb70bc60c7c6312"
POCKET_REPO = "kyutai/pocket-tts"
POCKET_REVISION = "3e82814a68665eec246ff649b14c71331f955c06"
TOKENIZER_REPO = "kyutai/pocket-tts-without-voice-cloning"
TOKENIZER_REVISION = "8843db76457a91db32077edf8dfcd1c0e3e755fd"


def bootstrap() -> None:
    environment = ROOT / ".venv-neural"
    python = environment / "bin/python"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    if Path(sys.prefix).resolve() != environment.resolve():
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-neural.lock")], check=True)
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    # Direct invocation with .venv-neural/bin/python must also explain missing deps.
    for name, version in (("sopro", "2.2.0"), ("pocket-tts", "4.0.1")):
        if importlib.metadata.version(name) != version:
            raise RuntimeError("Dépendances incohérentes : réinstaller requirements-neural.lock dans .venv-neural.")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install(source: Path, destination: Path, expected: str | None = None) -> dict:
    checksum = digest(source)
    if expected is not None and checksum != expected:
        raise ValueError(f"Référence Piper synthétique modifiée : {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if digest(destination) != checksum:
            raise RuntimeError(f"Fichier existant non conforme, laissé intact : {destination}")
    else:
        temporary = destination.with_name(destination.name + ".part")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    return {"sha256": checksum, "size": destination.stat().st_size}


def prepare(model_dir: Path, engine: str, verify_only: bool = False) -> None:
    sys.path.insert(0, str(ROOT))
    from voix_des_douze.neural_speech import MODELS, REFERENCES, SOPRO_FILES, _installed
    root = model_dir.expanduser().resolve() / MODELS[engine]
    if verify_only:
        manifest = _installed(root, engine)
        for name, item in manifest["files"].items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or digest(path) != item["sha256"]:
                raise ValueError(f"SHA256 incorrect : {path}")
        for name, _, expected in REFERENCES:
            if digest(root / "references" / name) != expected:
                raise ValueError("Référence synthétique incorrecte.")
        print(f"Vérifié hors réseau : {root}")
        return

    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import GatedRepoError
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    sources = {}

    def fetch(repo: str, revision: str, filename: str, target: str) -> None:
        try:
            source = Path(hf_hub_download(repo, filename, revision=revision))
        except GatedRepoError:
            raise RuntimeError(
                "Pocket français : accès HF refusé. Acceptez les conditions sur "
                "https://huggingface.co/kyutai/pocket-tts puis configurez un jeton autorisé. "
                "Aucun modèle sans clonage ni anglais ne sera substitué."
            ) from None
        files[target] = install(source, root / target)
        sources[target] = {"repository": repo, "revision": revision, "file": filename}
        print(f"Installé/vérifié : {target}", flush=True)

    if engine == "sopro":
        for name in (*SOPRO_FILES, "README.md"):
            fetch(SOPRO_REPO, SOPRO_REVISION, name, name)
    else:
        # Demander les poids gated avant tout le reste : erreur actionnable immédiate.
        fetch(POCKET_REPO, POCKET_REVISION, "languages/french/model.safetensors", "model.safetensors")
        fetch(TOKENIZER_REPO, TOKENIZER_REVISION, "languages/french/tokenizer.json", "tokenizer.json")
        fetch(POCKET_REPO, POCKET_REVISION, "README.md", "README.md")
        import yaml
        import pocket_tts
        bundled = Path(pocket_tts.__file__).parent / "config/french.yaml"
        config = yaml.safe_load(bundled.read_text(encoding="utf-8"))
        config["weights_path"] = str(root / "model.safetensors")
        config.pop("weights_path_without_voice_cloning", None)
        config["flow_lm"]["lookup_table"]["tokenizer_path"] = str(root / "tokenizer.json")
        path = root / "french.yaml"
        content = yaml.safe_dump(config, allow_unicode=True)
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise RuntimeError(f"Configuration existante différente, laissée intacte : {path}")
        path.write_text(content, encoding="utf-8")
        files["french.yaml"] = {"sha256": digest(path), "size": path.stat().st_size}
        sources["french.yaml"] = {"package": "pocket-tts==4.0.1", "config": "french", "paths": "local-only; no fallback"}

    for name, _, expected in REFERENCES:
        target = f"references/{name}"
        files[target] = install(ROOT / "samples" / name, root / target, expected)
        sources[target] = {"origin": "Piper synthetic output", "source": f"samples/{name}",
                           "text": "Bonjour aventurier. Herdegrize vous attend près du château !"}
    manifest = {"schema": 1, "engine": engine, "files": files, "sources": sources,
                "packages": {name: importlib.metadata.version(name) for name in ("sopro", "pocket-tts", "torch", "torchaudio")}}
    temporary = root / "installation.json.part"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(root / "installation.json")
    print(f"Prêt : {root}. Inférence : {ROOT / '.venv-neural/bin/python'}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("sopro", "pocket"))
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if not args.verify_only:
            bootstrap()
        prepare(args.model_dir, args.engine, args.verify_only)
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Préparation neuronale impossible : {error}\n")


if __name__ == "__main__":
    main()
