"""Sopro V2 Turbo / Pocket français, CPU local et références Piper synthétiques.

Préparer explicitement avec scripts/prepare_neural.py ; exécuter dans .venv-neural.
Aucun accès Hub à l'inférence : seuls chemins absolus locaux validés sont transmis.
Le débit est un rééchantillonnage (la hauteur varie aussi), pas un contrôle natif.
Une phrase complète est générée avant chaque segment ; ce n'est pas du streaming
acoustique. Aucun import torch/numpy ni chargement de poids à la découverte.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from fractions import Fraction
from pathlib import Path
from threading import Lock
from typing import Iterable, Iterator

from .speech import AudioSegment, ModelMissingError, VoiceChoice

MODELS = {"sopro": "sopro-v2-turbo", "pocket": "pocket-fr"}
SOPRO_FILES = ("config.json", "model.safetensors", "semantic_encoder.safetensors",
               "speaker_encoder.safetensors", "vocoder.safetensors", "tokenizer.model")
# WAV produits par scripts/prepare_models.py, versionnés dans samples/.
# Ces empreintes interdisent de remplacer une référence par un enregistrement humain.
REFERENCES = (
    ("fr_FR-siwis-medium-0.wav", "Siwis synthétique", "70e0525dba55e209be7b3ecd1f6b77bf718874fadd1bb399cb9d9c7d5dd7af2c"),
    ("fr_FR-mls-medium-0.wav", "MLS 1840 synthétique", "a568f9ab64a5fd90c09056c073b57eca376b0766f2fdf69d699db206b6b7681a"),
    ("fr_FR-mls-medium-1.wav", "MLS 3698 synthétique", "1201b3aee3930396b01767e386f5839e03fd8f186563517f4d613dba81cc275d"),
    ("fr_FR-tom-medium-0.wav", "Tom synthétique", "eb8dcf69a1e0850d05efe842887fd34c3793d38f128bcae48617f35bce7be339"),
)


def _root(model_dir: Path, engine: str) -> Path:
    if engine not in MODELS:
        raise ValueError("Moteur neuronal inconnu : choisir sopro ou pocket.")
    return Path(model_dir).expanduser().resolve() / MODELS[engine]


def _missing(path: Path) -> ModelMissingError:
    return ModelMissingError(f"Modèle neuronal absent ou incomplet : {path}. Exécutez scripts/prepare_neural.py.")


def _installed(root: Path, engine: str) -> dict:
    manifest = root / "installation.json"
    if not manifest.is_file():
        raise _missing(manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("engine") != engine:
        raise ValueError(f"Installation neuronale invalide : {manifest}")
    required = SOPRO_FILES if engine == "sopro" else ("french.yaml", "model.safetensors", "tokenizer.json")
    for name in (*required, *(f"references/{r[0]}" for r in REFERENCES)):
        item = data.get("files", {}).get(name)
        path = root / name
        if not item or not path.is_file() or path.stat().st_size != item["size"]:
            raise _missing(path)
    return data


def available_voices(model_dir: Path, engine: str) -> list[VoiceChoice]:
    """Quatre références synthétiques uniquement si l'installation est complète."""
    root = _root(model_dir, engine)
    try:
        _installed(root, engine)
    except ModelMissingError:
        return []
    prefix = "Sopro V2 Turbo" if engine == "sopro" else "Pocket français"
    return [VoiceChoice(f"{engine}:{name[:-4]}", f"{prefix} · {label}", MODELS[engine], index)
            for index, (name, label, _) in enumerate(REFERENCES)]


class NeuralSpeechEngine:
    """Chargement paresseux ; une session CPU et quatre conditionnements réutilisés.

    Les appels sont sérialisés par phrase, jamais de verrou conservé pendant yield.
    warmup amorce chaque référence demandée et ne lit aucun son matériellement.
    """

    def __init__(self, model_dir: Path, engine: str):
        self.model_dir = Path(model_dir)
        self.engine = engine
        self.root = _root(model_dir, engine)
        self._model = None
        self._references = {}
        self._lock = Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        data = _installed(self.root, self.engine)
        # Vérification unique avant chargement, y compris poids et références.
        for name, item in data["files"].items():
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError("Chemin de modèle hors installation.")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError(f"SHA256 neuronal incorrect : {path}")
        for name, _, digest in REFERENCES:
            if data["files"][f"references/{name}"]["sha256"] != digest:
                raise ValueError("Référence non synthétique refusée.")
        import torch
        torch.set_num_threads(2)
        if self.engine == "sopro":
            from sopro import SoproTTS
            model = SoproTTS.from_pretrained(self.root, device="cpu")
        else:
            import yaml
            from pocket_tts import TTSModel
            config_path = self.root / "french.yaml"
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            # Refuser toute URL et tout fallback Hub, même avec un cache HF rempli.
            def check_paths(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key.endswith("_path") and child is not None:
                            if not isinstance(child, str) or not Path(child).is_absolute():
                                raise ValueError("Pocket exige des chemins locaux absolus.")
                            path = Path(child).resolve()
                            if not path.is_relative_to(self.root) or not path.is_file():
                                raise _missing(path)
                        else:
                            check_paths(child)
            check_paths(config)
            if config.get("weights_path_without_voice_cloning") is not None:
                raise ValueError("Fallback Pocket interdit.")
            model = TTSModel.load_model(config=config_path)
            if not model.has_voice_cloning:
                raise ValueError("Les poids Pocket doivent permettre les références synthétiques.")
        self._model = model
        return model

    def _reference(self, index: int):
        if index not in self._references:
            import soundfile as sf
            import torch
            import torchaudio.functional as af
            audio, rate = sf.read(self.root / "references" / REFERENCES[index][0], dtype="float32")
            tensor = torch.from_numpy(audio).reshape(1, -1)
            model = self._load()
            if self.engine == "sopro":
                ref = model.prepare_reference(ref_audio=tensor, sample_rate=rate)
            else:
                tensor = af.resample(tensor, rate, model.sample_rate)
                ref = model.get_state_for_audio_prompt(tensor)
            self._references[index] = ref
        return self._references[index]

    def warmup(self, choices: Iterable[VoiceChoice]) -> None:
        warmed = set()
        for choice in choices:
            if choice.key not in warmed:
                for _ in self.synthesize("Bonjour.", choice):
                    pass
                warmed.add(choice.key)

    def synthesize(self, text: str, choice: VoiceChoice, speed: float = 1.0) -> Iterator[AudioSegment]:
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            raise ValueError("La vitesse doit être comprise entre 0,5 et 2,0.")
        if not text.strip():
            return
        if choice.model != MODELS[self.engine] or not 0 <= choice.speaker_id < len(REFERENCES):
            raise ValueError("Modèle ou locuteur neuronal invalide.")
        expected_key = f"{self.engine}:{REFERENCES[choice.speaker_id][0][:-4]}"
        if choice.key != expected_key:
            raise ValueError("Clé de voix neuronale incohérente avec le locuteur.")
        for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
            with self._lock:
                model = self._load()
                ref = self._reference(choice.speaker_id)
                if self.engine == "sopro":
                    audio = model.synthesize(sentence, ref=ref, lang="fr")
                else:
                    audio = model.generate_audio(ref, sentence, copy_state=True)
                import numpy as np
                signal = audio.detach().cpu().numpy().reshape(-1)
                if signal.size == 0 or not np.isfinite(signal).all():
                    raise RuntimeError("Le moteur neuronal a produit un signal vide ou non fini.")
                if speed != 1.0:
                    from scipy.signal import resample_poly
                    ratio = Fraction(1.0 / speed).limit_denominator(1000)
                    signal = resample_poly(signal, ratio.numerator, ratio.denominator)
                pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                segment = AudioSegment(pcm, model.sample_rate)
            yield segment
