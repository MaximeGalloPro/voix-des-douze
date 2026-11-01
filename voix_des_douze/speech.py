"""Synthèse Piper locale : PCM signé 16 bits little-endian, mono, CPU uniquement.

Aucun téléchargement ici. Préparer les modèles avec scripts/prepare_models.py.
Un SpeechEngine conserve une session par modèle ; les locuteurs MLS la partagent.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable, Iterator


class ModelMissingError(FileNotFoundError):
    """Modèle ou configuration non installé localement."""


@dataclass(frozen=True)
class VoiceChoice:
    key: str
    label: str
    model: str
    speaker_id: int


@dataclass(frozen=True)
class AudioSegment:
    pcm: bytes
    sample_rate: int


_MODELS = ("fr_FR-siwis-medium", "fr_FR-mls-medium", "fr_FR-tom-medium")


def _config(model_dir: Path, model: str) -> dict:
    if model not in _MODELS:
        raise ValueError("Modèle français non pris en charge.")
    for suffix in (".onnx", ".onnx.json"):
        path = model_dir / (model + suffix)
        if not path.is_file():
            raise ModelMissingError(
                f"Modèle vocal absent : {path}. Exécutez scripts/prepare_models.py."
            )
    data = json.loads((model_dir / (model + ".onnx.json")).read_text(encoding="utf-8"))
    if data.get("language", {}).get("code") != "fr_FR" or data.get("espeak", {}).get("voice") != "fr":
        raise ValueError(f"Configuration non française : {model}.")
    return data


def available_voices(model_dir: Path) -> list[VoiceChoice]:
    """Choix installés et identifiants vérifiés dans les configurations réelles.

    MLS expose ici deux locuteurs, pas une promesse de qualité pour ses 125 voix.
    Les numéros affichés sont les identifiants du corpus ; speaker_id est l'index ONNX.
    """
    result = []
    for model in _MODELS:
        try:
            config = _config(Path(model_dir), model)
        except ModelMissingError:
            continue
        if model == "fr_FR-mls-medium":
            speakers = config["speaker_id_map"]
            for corpus_id in ("1840", "3698"):
                speaker_id = speakers[corpus_id]
                if not 0 <= speaker_id < config["num_speakers"]:
                    raise ValueError("Identifiant de locuteur MLS invalide.")
                result.append(VoiceChoice(f"{model}:{speaker_id}", f"MLS · {corpus_id}", model, speaker_id))
        else:
            if config["num_speakers"] != 1:
                raise ValueError(f"Nombre de locuteurs inattendu : {model}.")
            label = "Siwis" if "siwis" in model else "Tom"
            result.append(VoiceChoice(f"{model}:0", label, model, 0))
    return result


class SpeechEngine:
    """À appeler dans un worker ; validation et inférence ne bloquent pas l'UI.

    Piper segmente par phrase et livre le premier segment sans synthétiser la suite.
    Le verrou n'est jamais conservé pendant yield : un lecteur annulé ne bloque pas
    la génération suivante. L'UI doit invalider les segments des anciens travaux.
    """

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self._voices = {}
        self._lock = Lock()

    def _load(self, model: str):
        if model in self._voices:
            return self._voices[model]
        data = _config(self.model_dir, model)
        from onnxruntime import InferenceSession, SessionOptions
        from piper import PiperVoice
        from piper.config import PiperConfig

        options = SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        voice = PiperVoice(
            session=InferenceSession(
                str(self.model_dir / (model + ".onnx")),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            ),
            config=PiperConfig.from_dict(data),
            download_dir=self.model_dir,
        )
        self._voices[model] = voice
        return voice

    def warmup(self, choices: Iterable[VoiceChoice]) -> None:
        """Charger et amorcer chaque modèle unique, sans lecture audio.

        Méthode bloquante à exécuter dans le worker avant d'annoncer « prêt ».
        Les erreurs de modèle remontent normalement ; aucune connexion réseau.
        """
        warmed = set()
        for choice in choices:
            if choice.model in warmed:
                continue
            for _segment in self.synthesize("Bonjour.", choice):
                pass
            warmed.add(choice.model)

    def synthesize(self, text: str, choice: VoiceChoice, speed: float = 1.0) -> Iterator[AudioSegment]:
        """Vitesse 0,5–2,0 ; texte blanc produit zéro segment, sans charger de modèle."""
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            raise ValueError("La vitesse doit être comprise entre 0,5 et 2,0.")
        text = text.strip()
        if not text:
            return
        from piper import SynthesisConfig

        with self._lock:
            voice = self._load(choice.model)
            if not 0 <= choice.speaker_id < voice.config.num_speakers:
                raise ValueError("Identifiant de locuteur invalide pour ce modèle.")
            config = SynthesisConfig(
                speaker_id=choice.speaker_id,
                length_scale=voice.config.length_scale / speed,
            )
            chunks = voice.synthesize(text, syn_config=config)
        while True:
            with self._lock:
                chunk = next(chunks, None)
                if chunk is None:
                    return
                segment = AudioSegment(chunk.audio_int16_bytes, chunk.sample_rate)
            yield segment
