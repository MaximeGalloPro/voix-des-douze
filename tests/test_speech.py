"""Contrats publics et intégration Piper réelle, sans socket autorisé.

Les tests réels utilisent VOIX_MODEL_DIR ou le cache standard ; ils sont ignorés
explicitement si les modèles n'ont pas encore été préparés.
"""
import math
import os
import socket
from array import array
from pathlib import Path

import pytest

from voix_des_douze.speech import ModelMissingError, SpeechEngine, VoiceChoice, available_voices


CHOICE = VoiceChoice("fr_FR-siwis-medium:0", "Siwis", "fr_FR-siwis-medium", 0)


def test_missing_model_explains_preparation(tmp_path):
    assert available_voices(tmp_path) == []
    with pytest.raises(ModelMissingError, match="Modèle vocal absent.*prepare_models.py"):
        list(SpeechEngine(tmp_path).synthesize("Bonjour.", CHOICE))


def test_blank_text_needs_no_model(tmp_path):
    assert list(SpeechEngine(tmp_path).synthesize(" \n\t ", CHOICE)) == []


@pytest.mark.parametrize("speed", [0, -1, 0.49, 2.01, math.inf, math.nan])
def test_unsafe_speed_rejected_before_model_access(tmp_path, speed):
    with pytest.raises(ValueError, match="vitesse"):
        list(SpeechEngine(tmp_path).synthesize("Bonjour.", CHOICE, speed))


@pytest.fixture
def real_models():
    directory = Path(os.environ.get("VOIX_MODEL_DIR", Path.home() / "Library/Caches/VoixDesDouze/models"))
    if len(available_voices(directory)) != 4:
        pytest.skip("Modèles français non préparés : scripts/prepare_models.py")
    return directory


@pytest.fixture
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Accès réseau interdit pendant la synthèse locale")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


def test_real_four_voices_offline_pcm_and_differences(real_models, deny_network):
    engine = SpeechEngine(real_models)
    voices = available_voices(real_models)
    engine.warmup(iter(voices))
    mls = [v for v in voices if "mls" in v.model]
    assert {v.speaker_id for v in mls} == {0, 1}
    signals = []
    for voice in voices:
        segments = list(engine.synthesize("Bonjour aventurier. La quête commence ici !", voice))
        assert len(segments) == 2
        assert {segment.sample_rate for segment in segments} == ({44100} if "tom" in voice.model else {22050})
        pcm = b"".join(segment.pcm for segment in segments)
        assert len(pcm) % 2 == 0
        samples = array("h", pcm)
        duration = len(samples) / segments[0].sample_rate
        assert 1 < duration < 30
        assert max(samples) - min(samples) > 1000
        signals.append(pcm)
    assert len(set(signals)) == 4


def test_real_speed_changes_audio_duration(real_models, deny_network):
    engine = SpeechEngine(real_models)
    text = "Les aventuriers se retrouvent devant la porte du château."
    slow = list(engine.synthesize(text, CHOICE, speed=0.5))
    fast = list(engine.synthesize(text, CHOICE, speed=2.0))
    slow_duration = sum(len(s.pcm) / (2 * s.sample_rate) for s in slow)
    fast_duration = sum(len(s.pcm) / (2 * s.sample_rate) for s in fast)
    assert slow_duration > 2.5 * fast_duration


def test_real_invalid_speaker_is_actionable(real_models, deny_network):
    invalid = VoiceChoice("bad", "Invalide", CHOICE.model, 1)
    with pytest.raises(ValueError, match="locuteur"):
        list(SpeechEngine(real_models).synthesize("Bonjour.", invalid))
