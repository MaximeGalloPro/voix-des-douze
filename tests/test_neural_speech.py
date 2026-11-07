"""Contrats et vraie synthèse CPU Sopro/Pocket FR, sockets Python interdits.

.venv-neural/bin/python -m pytest tests/test_neural_speech.py -v
VOIX_MODEL_DIR surcharge le cache standard. Sans poids préparés, les cas réels
sont explicitement ignorés ; VOIX_REQUIRE_NEURAL=1 transforme ce manque en échec.
Aucun faux moteur ni résultat PCM préfabriqué.
"""
import math
import os
import socket
from array import array
from dataclasses import replace
from pathlib import Path

import pytest

from voix_des_douze.neural_speech import MODELS, NeuralSpeechEngine, available_voices
from voix_des_douze.speech import ModelMissingError, VoiceChoice


@pytest.fixture(params=("sopro", "pocket"))
def engine_name(request):
    return request.param


def choice(engine):
    return VoiceChoice(f"{engine}:fr_FR-siwis-medium-0", "Siwis synthétique", MODELS[engine], 0)


def test_missing_installation_is_actionable(tmp_path, engine_name):
    assert available_voices(tmp_path, engine_name) == []
    with pytest.raises(ModelMissingError, match="prepare_neural.py"):
        list(NeuralSpeechEngine(tmp_path, engine_name).synthesize("Bonjour.", choice(engine_name)))


def test_blank_needs_neither_model_nor_dependencies(tmp_path, engine_name):
    assert list(NeuralSpeechEngine(tmp_path, engine_name).synthesize(" \n\t", choice(engine_name))) == []


@pytest.mark.parametrize("speed", [0, -1, 0.49, 2.01, math.inf, math.nan])
def test_unsafe_speed_rejected(tmp_path, engine_name, speed):
    with pytest.raises(ValueError, match="vitesse"):
        list(NeuralSpeechEngine(tmp_path, engine_name).synthesize("Bonjour.", choice(engine_name), speed))


def test_unknown_engine_rejected(tmp_path):
    with pytest.raises(ValueError, match="Moteur"):
        available_voices(tmp_path, "english")
    with pytest.raises(ValueError, match="Moteur"):
        NeuralSpeechEngine(tmp_path, "english")


@pytest.mark.parametrize("change", [{"model": "english"}, {"speaker_id": -1}, {"speaker_id": 4}, {"key": "wrong"}])
def test_wrong_voice_rejected_before_loading(tmp_path, engine_name, change):
    with pytest.raises(ValueError, match="locuteur"):
        list(NeuralSpeechEngine(tmp_path, engine_name).synthesize("Bonjour.", replace(choice(engine_name), **change)))


@pytest.fixture(scope="module", params=("sopro", "pocket"))
def real_engine(request):
    name = request.param
    directory = Path(os.environ.get("VOIX_MODEL_DIR", Path.home() / "Library/Caches/VoixDesDouze/models"))
    voices = available_voices(directory, name)
    if not voices:
        message = f"Poids {name} non préparés : scripts/prepare_neural.py --engine {name}"
        if os.environ.get("VOIX_REQUIRE_NEURAL") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return NeuralSpeechEngine(directory, name), voices


@pytest.fixture
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Connexion interdite pendant l'inférence neuronale")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    with pytest.raises(AssertionError, match="Connexion interdite"):
        socket.create_connection(("example.org", 443))


def pcm_duration(segments):
    return sum(len(segment.pcm) / (2 * segment.sample_rate) for segment in segments)


def test_real_french_four_synthetic_references_offline(real_engine, offline):
    engine, voices = real_engine
    assert {v.speaker_id for v in voices} == {0, 1, 2, 3}
    engine.warmup(iter(voices))
    for voice in voices:
        segments = list(engine.synthesize("Bonjour aventurier. La quête commence au château !", voice))
        assert len(segments) == 2
        assert {s.sample_rate for s in segments} == {24000}
        assert 1.0 < pcm_duration(segments) < 30
        for segment in segments:
            assert len(segment.pcm) % 2 == 0
            signal = array("h", segment.pcm)
            assert max(signal) - min(signal) > 1000
            rms = math.sqrt(sum(x * x for x in signal) / len(signal))
            assert rms > 100


def test_real_speed_scales_pcm_duration_offline(real_engine, offline):
    import torch
    engine, voices = real_engine
    # Même bruit de génération : seules les vitesses post-génération diffèrent.
    text = "Les aventuriers attendent devant la porte du château."
    torch.manual_seed(42)
    slow = list(engine.synthesize(text, voices[0], speed=0.5))
    torch.manual_seed(42)
    fast = list(engine.synthesize(text, voices[0], speed=2.0))
    assert pcm_duration(slow) == pytest.approx(4 * pcm_duration(fast), abs=0.01)
    assert slow[0].sample_rate == fast[0].sample_rate == 24000


def test_preparation_refuses_non_synthetic_audio(tmp_path):
    from scripts.prepare_neural import install
    source = tmp_path / "human.wav"
    source.write_bytes(b"unapproved recording")
    with pytest.raises(ValueError, match="synthétique"):
        install(source, tmp_path / "reference.wav", "0" * 64)
    assert not (tmp_path / "reference.wav").exists()


def test_preparation_preserves_unknown_existing_file(tmp_path):
    from scripts.prepare_neural import install
    source = tmp_path / "download"
    source.write_bytes(b"expected weights")
    destination = tmp_path / "model"
    destination.write_bytes(b"user data")
    with pytest.raises(RuntimeError, match="laissé intact"):
        install(source, destination)
    assert destination.read_bytes() == b"user data"
