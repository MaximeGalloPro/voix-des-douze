import threading
from types import SimpleNamespace
from unittest.mock import patch
import wave

from voix_des_douze.playback import Playback, Utterance


def test_cancel_during_generation_never_starts_old_audio():
    entered, release, new_played = threading.Event(), threading.Event(), threading.Event()
    played = []

    class Engine:
        def synthesize(self, text, voice, speed):
            if text == "ancien":
                entered.set()
                assert release.wait(3)
            yield SimpleNamespace(pcm=(b"\x01\x00" if text == "ancien" else b"\x02\x00") * 100,
                                  sample_rate=22050)

    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            with wave.open(command[-1], "rb") as stream:
                played.append(stream.readframes(100))
            if played[-1].startswith(b"\x02\x00"):
                new_played.set()
        def poll(self):
            return 0
        def communicate(self):
            return b"", b""
        def kill(self):
            raise AssertionError("No active process in this scenario")

    with patch("voix_des_douze.playback.subprocess.Popen", Process):
        player = Playback(Engine())
        try:
            player.submit([Utterance("ancien", None)])
            assert entered.wait(3)
            player.cancel()
            player.submit([Utterance("nouveau", None)])
            release.set()
            assert new_played.wait(3)
            assert played == [b"\x02\x00" * 100]
        finally:
            release.set()
            player.close()


def test_stop_terminates_active_audio():
    playing, terminated = threading.Event(), threading.Event()

    class Engine:
        def synthesize(self, *_):
            yield SimpleNamespace(pcm=b"\x01\x00" * 100, sample_rate=22050)

    class Process:
        returncode = 0
        def __init__(self, *args, **kwargs):
            playing.set()
        def poll(self):
            return None if not terminated.is_set() else -9
        def kill(self):
            terminated.set()
        def communicate(self):
            assert terminated.wait(3)
            return b"", b""

    with patch("voix_des_douze.playback.subprocess.Popen", Process):
        player = Playback(Engine())
        try:
            player.submit([Utterance("dialogue long", None)])
            assert playing.wait(3)
            player.cancel()
            assert terminated.wait(1)
        finally:
            player.close()
