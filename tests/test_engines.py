"""Vrais sous-processus de synthèse ; aucun téléchargement implicite."""
from array import array
import os
from pathlib import Path
import threading
import time

import pytest

from voix_des_douze.engines import ManagedSpeechEngine, available_voices
from voix_des_douze.speech import VoiceChoice


@pytest.fixture(params=["piper", "sopro", "pocket"])
def prepared_engine(request):
    directory = Path(os.environ.get("VOIX_MODEL_DIR", Path.home() / "Library/Caches/VoixDesDouze/models"))
    voices = available_voices(directory, request.param)
    if not voices:
        pytest.skip(f"Préparer explicitement les poids {request.param} avant ce test réel.")
    engine = ManagedSpeechEngine(directory, request.param)
    try:
        yield engine, voices
    finally:
        engine.close()


def test_each_real_engine_offline_worker_audio_and_reuse(prepared_engine):
    engine, voices = prepared_engine
    engine.warmup(voices[:1])
    process = engine._process
    for text in ("Bonjour aventurier, la quête commence ici.", "Attention ! Le château est fermé."):
        segments = list(engine.synthesize(text, voices[0]))
        rates = {segment.sample_rate for segment in segments}
        assert len(rates) == 1
        pcm = b"".join(segment.pcm for segment in segments)
        assert len(pcm) % 2 == 0
        signal = array("h", pcm)
        assert 0.5 < len(signal) / rates.pop() < 40
        assert max(signal) - min(signal) > 1000
        assert engine._process is process  # Le second dialogue garde le modèle résident.
    engine.close()
    assert process.poll() is not None
    with pytest.raises(RuntimeError, match="fermé"):
        list(engine.synthesize("Texte après fermeture.", voices[0]))


def test_each_real_engine_cancellation_discards_buffer_and_recovers(prepared_engine):
    engine, voices = prepared_engine
    stream = engine.synthesize("Le marchand ouvre la porte. " * 8, voices[0])
    next(stream)
    previous = engine._process
    engine.abort()
    with pytest.raises(RuntimeError, match="annulée"):
        next(stream)
    assert previous.poll() is not None
    recovered = list(engine.synthesize("La lecture reprend avec ce nouveau texte.", voices[0]))
    assert engine._process is not previous
    signal = array("h", b"".join(segment.pcm for segment in recovered))
    assert max(signal) - min(signal) > 1000


def test_missing_piper_worker_reports_error_without_substitution(tmp_path):
    engine = ManagedSpeechEngine(tmp_path, "piper")
    choice = VoiceChoice("absent", "Siwis", "fr_FR-siwis-medium", 0)
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="Modèle vocal absent"):
                list(engine.synthesize("Bonjour.", choice))
    finally:
        engine.close()


def test_each_real_engine_interrupts_inference_before_first_audio(prepared_engine):
    engine, voices = prepared_engine
    engine.warmup(voices[:1])
    outcome = []
    def generate():
        try:
            outcome.extend(engine.synthesize(
                "Le marchand vous accueille devant la grande porte du château et vous raconte "
                "les aventures des voyageurs qui ont traversé les montagnes pendant la nuit.", voices[0]))
        except RuntimeError as error:
            outcome.append(str(error))
    thread = threading.Thread(target=generate)
    thread.start()
    deadline = time.monotonic() + 5
    while not engine._busy and time.monotonic() < deadline:
        time.sleep(0.001)
    assert engine._busy
    process = engine._process
    engine.abort()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert process.poll() is not None
    assert outcome == ["Synthèse annulée."]


def test_each_real_engine_abandoned_iterator_never_leaks_audio_to_next_request(prepared_engine):
    engine, voices = prepared_engine
    old = engine.synthesize("Bonjour aventurier. La quête commence ici. Le château vous attend.", voices[0])
    next(old)
    process = engine._process
    old.close()
    assert process.poll() is not None
    assert list(engine.synthesize("", voices[0])) == []
    current = list(engine.synthesize("Voici la nouvelle réplique.", voices[0]))
    signal = array("h", b"".join(segment.pcm for segment in current))
    assert max(signal) - min(signal) > 1000


def test_each_real_engine_cancellation_before_iteration_invalidates_request(prepared_engine):
    engine, voices = prepared_engine
    engine.warmup(voices[:1])
    process = engine._process
    old = engine.synthesize("Cette réplique ne doit jamais être générée.", voices[0])
    engine.abort()
    with pytest.raises(RuntimeError, match="annulée"):
        list(old)
    assert engine._process is process  # Annuler au repos ne décharge pas le modèle.
    assert list(engine.synthesize("", voices[0])) == []
