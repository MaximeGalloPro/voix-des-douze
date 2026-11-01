"""Image Dofus réelle recadrée → Vision réel → attribution persistante → Piper réel."""
from array import array
from pathlib import Path
import platform
import socket
import wave

from PIL import Image
import pytest
from voix_des_douze.settings import Store, model_directory
from voix_des_douze.speech import SpeechEngine, available_voices
from voix_des_douze.vision import recognize


@pytest.mark.skipif(platform.system() != "Darwin", reason="Apple Vision exige macOS")
def test_real_image_to_persistent_character_to_wav_offline(tmp_path, monkeypatch):
    models = model_directory()
    voices = available_voices(models)
    if not voices:
        pytest.skip("Préparez les modèles avec Installer.command")

    def deny(*args, **kwargs):
        raise AssertionError("Le chemin OCR/TTS local ne doit pas utiliser le réseau Python")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    canvas = Image.new("RGB", (2048, 1280))
    fixture = Path(__file__).parent / "fixtures/dialogues/al.png"
    with Image.open(fixture) as crop:
        canvas.paste(crop, (1290, 330))
        canvas.info.update(crop.info)
    dialogue = recognize(canvas)
    assert dialogue.npc == "Al Etsop"
    assert "0 Kamas" in dialogue.text
    store = Store(tmp_path / "settings.json")
    assert tuple(t.replace("’", "'") for t in dialogue.responses) == (
        "Consulter son coffre personnel.",
        "Se renseigner sur les modalités d'ouverture d'un compte en banque.")
    choice = store.voice_for(dialogue.npc, [v.key for v in voices])
    segments = list(SpeechEngine(models).synthesize(dialogue.text, next(v for v in voices if v.key == choice)))
    output = tmp_path / "integration.wav"
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(segments[0].sample_rate)
        for segment in segments:
            wav.writeframes(segment.pcm)
    with wave.open(str(output), "rb") as wav:
        signal = array("h", wav.readframes(wav.getnframes()))
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert 5 < wav.getnframes() / wav.getframerate() < 90
        assert max(signal) - min(signal) > 1000
    assert Store(tmp_path / "settings.json").voice_for("Al Etsop", [v.key for v in voices]) == choice
