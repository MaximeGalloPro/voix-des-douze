"""Arrival boundaries and cancellation, without sending input to a game."""
from types import SimpleNamespace
import time

import pytest
from PySide6.QtWidgets import QApplication, QWidget, QCheckBox, QLabel
from PySide6.QtTest import QTest

from voix_des_douze.harvest import Resource, ResourceSnapshot
from voix_des_douze.overlay import screen_key
from voix_des_douze.resource_click import FirstResourceClick


@pytest.fixture
def assist():
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.first_resource = QCheckBox(w)
    w.first_resource.setChecked(True)
    w.accepting, w.paused = True, False
    w.overlay = SimpleNamespace(pid=123, foreground=lambda: 123, calibrating=False)
    w.status = QLabel(w)
    errors, clicks = [], []
    w.show_error = errors.append
    screen = w.screen()
    key = screen_key(screen)
    w.store = SimpleNamespace(settings=SimpleNamespace(
        overlay_screen=key, overlay_adjustments={key: [0, 0, 1]}))
    controller = FirstResourceClick(w, lambda *args: clicks.append(args))
    controller.timer.setInterval(10)
    yield w, controller, clicks, errors
    controller.clear()
    w.close()


def snapshot(map_id, state=0, enabled=True, cell=280):
    return ResourceSnapshot(map_id, True, (Resource(1, 1, "Blé", cell, state, enabled),), 0, "jnj")


def wait_for_timer(controller):
    deadline = time.monotonic() + 2
    while controller.timer.isActive() and time.monotonic() < deadline:
        QTest.qWait(20)
    return not controller.timer.isActive()


def test_first_sync_is_silent_and_arrival_clicks_once(assist):
    w, c, clicks, errors = assist
    c.update(snapshot(1))
    assert wait_for_timer(c)
    assert clicks == []
    c.update(snapshot(2))
    assert wait_for_timer(c)
    assert len(clicks) == 1
    assert clicks[0][0] == 123
    assert clicks[0][2].contains(clicks[0][1].toPoint())
    c.update(snapshot(2))
    assert wait_for_timer(c)
    assert len(clicks) == 1
    c.update(snapshot(1))
    assert wait_for_timer(c)
    assert len(clicks) == 2
    assert errors == []


@pytest.mark.parametrize("reason", ["disabled", "stopped", "paused", "background", "calibrating", "clear", "depleted", "unavailable", "invalid_cell", "uncalibrated"])
def test_pending_arrival_rechecks_latest_state_and_safety(assist, reason):
    w, c, clicks, errors = assist
    c.update(snapshot(1))
    c.update(snapshot(2))
    if reason == "disabled":
        w.first_resource.setChecked(False)
    elif reason == "stopped":
        w.accepting = False
    elif reason == "paused":
        w.paused = True
    elif reason == "background":
        w.overlay.foreground = lambda: 456
    elif reason == "calibrating":
        w.overlay.calibrating = True
    elif reason == "clear":
        c.clear()
    elif reason == "depleted":
        c.update(snapshot(2, state=1))
    elif reason == "unavailable":
        c.update(snapshot(2, enabled=False))
    elif reason == "invalid_cell":
        c.update(snapshot(2, cell=560))
    elif reason == "uncalibrated":
        w.store.settings.overlay_adjustments.clear()
    assert wait_for_timer(c)
    assert clicks == []


def test_new_map_replaces_pending_target(assist):
    w, c, clicks, errors = assist
    c.update(snapshot(1))
    c.update(snapshot(2, cell=280))
    c.update(snapshot(3, cell=281))
    assert wait_for_timer(c)
    assert len(clicks) == 1
    assert "case 281" in w.status.text()
