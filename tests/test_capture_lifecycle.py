"""Supervision réelle de sous-processus, sans capture réseau ni privilèges."""
import os
import subprocess
import sys
import threading
import time

import pytest

from voix_des_douze.native.network_capture import supervise


def sleeper(ignore_term=False):
    code = (
        "import signal,time; "
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
        + "print('ready', flush=True); time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert child.stdout.readline() == b"ready\n"
    return child


@pytest.mark.parametrize("reason", ["owner_died", "stop", "channel_removed"])
def test_capture_stops_when_session_disappears(tmp_path, reason):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    owner, capture = sleeper(), sleeper()
    try:
        if reason == "owner_died":
            owner.kill()
            owner.wait(timeout=3)
        elif reason == "stop":
            (tmp_path / "stop").touch()
        else:
            heartbeat.unlink()
        supervise(capture, tmp_path, owner.pid, os.getpid())
        assert capture.returncode == -15
    finally:
        for child in (owner, capture):
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)


def test_stalled_live_owner_expires_and_stubborn_capture_is_killed(tmp_path):
    (tmp_path / "heartbeat").touch()
    capture = sleeper(ignore_term=True)
    try:
        supervise(capture, tmp_path, os.getpid(), os.getpid(), lease_timeout=0.3)
        assert capture.returncode == -9
    finally:
        if capture.poll() is None:
            capture.kill()
        capture.communicate(timeout=3)


def test_renewed_lease_keeps_capture_until_explicit_stop(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    capture = sleeper()
    worker = threading.Thread(target=supervise, args=(capture, tmp_path, os.getpid(), os.getpid(), 0.6))
    worker.start()
    try:
        for _ in range(8):
            heartbeat.touch()
            time.sleep(0.15)
        assert capture.poll() is None
        (tmp_path / "stop").touch()
        worker.join(timeout=4)
        assert not worker.is_alive()
        assert capture.returncode == -15
    finally:
        (tmp_path / "stop").touch()
        worker.join(timeout=4)
        if capture.poll() is None:
            capture.kill()
        capture.communicate(timeout=3)
