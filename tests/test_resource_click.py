"""Arrival boundaries and cancellation, without sending input to a game."""
from types import SimpleNamespace
import time

import pytest
from PySide6.QtWidgets import QApplication, QWidget, QCheckBox, QLabel
from PySide6.QtTest import QTest
from shiboken6 import delete

from voix_des_douze.harvest import Resource, ResourceSnapshot
from voix_des_douze.overlay import ResourceOverlay, screen_key
from voix_des_douze.resource_click import FirstResourceClick
from voix_des_douze.settings import Settings


@pytest.fixture(scope="session")
def click_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def assist(click_app):
    w = QWidget()
    w.first_resource = QCheckBox(w)
    w.first_resource.setChecked(True)
    w.accepting, w.paused = True, False
    w.overlay = ResourceOverlay()
    w.overlay.pid = 123
    w.overlay.foreground = lambda: 123
    w.status = QLabel(w)
    errors, clicks = [], []
    w.show_error = errors.append
    screen = w.screen()
    key = screen_key(screen)
    w.store = SimpleNamespace(settings=Settings(
        overlay_screen=key, overlay_adjustments={key: [0, 0, 1]}))
    controller = FirstResourceClick(w, lambda *args: clicks.append(args))
    controller.timer.setInterval(10)
    yield w, controller, clicks, errors
    controller.clear()
    controller.stop_debug()
    w.overlay.timer.stop()
    w.overlay.close()
    w.close()
    # Close only hides Qt windows; destroy native objects before wrapper GC.
    delete(w.overlay)
    delete(w)


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


def test_auxiliary_titlebar_does_not_hide_fullscreen_click_target():
    from PySide6.QtCore import QPointF, QRect
    from voix_des_douze.resource_click import check_click_window

    rows = [
        {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 123,
         "kCGWindowBounds": {"X": 0, "Y": -32, "Width": 2048, "Height": 32}},
        {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 123,
         "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 2048, "Height": 1280}},
    ]
    geometry = QRect(0, 0, 2048, 1280)
    point = QPointF(1000, 600)
    check_click_window(rows, 123, point, geometry)

    # A different app covering the target must still block the click.
    rows.insert(0, {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 456,
                    "kCGWindowOwnerName": "Autre application",
                    "kCGWindowBounds": {"X": 900, "Y": 500, "Width": 200, "Height": 200}})
    with pytest.raises(RuntimeError, match="Autre application"):
        check_click_window(rows, 123, point, geometry)

    rows.pop(0)
    rows[1]["kCGWindowBounds"]["Height"] = 1100
    with pytest.raises(RuntimeError, match="Height=1100"):
        check_click_window(rows, 123, point, geometry)

    with pytest.raises(RuntimeError, match="inconnue"):
        check_click_window([], 123, point, geometry)


def test_manual_debug_first_target_without_automatic_and_logs_blockers(assist, tmp_path):
    import json

    w, c, clicks, errors = assist
    w.first_resource.setChecked(False)
    first = snapshot(1).resources[0]
    second = Resource(2, 1, "Orge", 281, 0, True)
    c.update(ResourceSnapshot(1, True, (first, second), 0, "jnj"))
    c.manual_click()
    assert clicks == []  # Debug opt-in is required.
    c.start_debug(tmp_path)
    c.manual_click()
    assert "Blé, case 280" in w.status.text()
    assert len(clicks) == 1
    w.overlay.foreground = lambda: 456
    c.debug_timer.stop()
    c.debug_next()
    assert len(clicks) == 1
    assert "hors premier plan" in errors[-1]
    path = c.debug_path
    c.stop_debug()
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(r["étape"] == "cible" and r["élément"] == 1 for r in records)
    assert any(r["étape"] == "bloqué" and "hors premier plan" in r["raison"] for r in records)
    before = path.read_bytes()
    c.manual_click()
    assert path.read_bytes() == before
    assert len(clicks) == 1


def test_debug_cross_matches_sent_point_and_clears_on_map_change_or_disable(assist, tmp_path):
    from PySide6.QtCore import QPointF

    w, c, clicks, errors = assist
    c.update(snapshot(1))
    c.click()
    assert w.overlay.click_target is None
    c.start_debug(tmp_path)
    c.manual_click()
    point, geometry = clicks[-1][1:]
    assert w.overlay.click_target + QPointF(geometry.topLeft()) == point
    assert w.overlay.isVisible()  # Resource markers are disabled.
    c.update(snapshot(2))
    assert w.overlay.click_target is None
    assert not w.overlay.isVisible()
    c.timer.stop()
    c.manual_click()
    assert w.overlay.isVisible()
    c.stop_debug()
    assert w.overlay.click_target is None
    assert not w.overlay.isVisible()


def test_focus_lost_during_native_window_check_blocks_global_click():
    import ctypes
    import plistlib
    from PySide6.QtCore import QPointF, QRect
    from voix_des_douze.resource_click import MacClick

    # The controller accepted focus, but it changes before global injection.
    payload = plistlib.dumps([{
        "kCGWindowLayer": 0, "kCGWindowOwnerPID": 123,
        "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 800, "Height": 600},
    }])
    buffer = ctypes.create_string_buffer(payload)
    posted = []
    click = MacClick.__new__(MacClick)
    click.trace = lambda *args, **fields: None
    click.foreground = lambda: 456
    click.lib = SimpleNamespace(
        AXIsProcessTrusted=lambda: True,
        CGWindowListCopyWindowInfo=lambda *args: 1,
        CFPropertyListCreateData=lambda *args: 2,
        CFDataGetBytePtr=lambda *args: ctypes.addressof(buffer),
        CFDataGetLength=lambda *args: len(payload),
        CFRelease=lambda *args: None,
        CGEventCreateMouseEvent=lambda *args: 3,
        CGEventSetIntegerValueField=lambda *args: None,
        CGEventSetFlags=lambda *args: None,
        CGEventPost=lambda *args: posted.append(args),
    )
    with pytest.raises(RuntimeError, match="hors premier plan"):
        click(123, QPointF(400, 300), QRect(0, 0, 800, 600))
    assert posted == []


def test_debug_sequence_visits_each_remaining_target_once(assist, tmp_path):
    w, c, clicks, errors = assist
    w.first_resource.setChecked(False)
    resources = (
        Resource(1, 1, "Blé", 280, 0, True),
        Resource(2, 1, "Orge", 281, 0, True),
        Resource(3, 1, "Sauge", 282, 0, True),
    )
    c.update(ResourceSnapshot(1, True, resources, 0, "jnj"))
    c.start_debug(tmp_path)
    c.manual_click()
    assert len(clicks) == 1
    assert 700 <= c.debug_timer.interval() <= 1000
    # The second resource disappears before its turn; do not click it or new arrivals.
    c.update(ResourceSnapshot(1, False, (resources[0], resources[2],
        Resource(4, 1, "Frêne", 283, 0, True)), 0, "jnj"))
    c.debug_timer.stop()
    c.debug_next()
    assert len(clicks) == 2
    assert "Sauge, case 282" in w.status.text()
    assert not c.debug_targets and not c.debug_timer.isActive()
    assert errors == []


@pytest.mark.parametrize("action", ["key", "map", "stop", "clear"])
def test_debug_sequence_cancellation_discards_remaining_targets(assist, tmp_path, action):
    w, c, clicks, errors = assist
    resources = (snapshot(1).resources[0], Resource(2, 1, "Orge", 281, 0, True))
    c.update(ResourceSnapshot(1, True, resources, 0, "jnj"))
    c.start_debug(tmp_path)
    c.manual_click()
    if action == "key":
        c.manual_click()
    elif action == "map":
        c.update(snapshot(2))
        assert 700 <= c.timer.interval() <= 850
    elif action == "stop":
        c.stop_debug()
    else:
        c.clear()
    assert not c.debug_timer.isActive()
    assert not c.debug_targets
    c.debug_next()
    assert len(clicks) == 1


def test_idle_waits_for_new_availability_and_does_not_repeat(assist, monkeypatch):
    w, c, clicks, errors = assist
    now = [100.0]
    monkeypatch.setattr("voix_des_douze.resource_click.time.monotonic", lambda: now[0])
    monkeypatch.setattr("voix_des_douze.resource_click.random.randint", lambda a, b: 1200)
    c.update(snapshot(1, state=1))
    c.toggle_idle()
    c.update(snapshot(1))
    assert c.idle_pending == {1: 101.2}
    c.idle_next()
    assert clicks == []
    now[0] = 101.2
    c.idle_next()
    assert len(clicks) == 1
    c.update(snapshot(1))
    c.idle_next()
    assert len(clicks) == 1
    c.update(snapshot(1, state=1))
    c.update(snapshot(1))
    now[0] = 102.4
    c.idle_next()
    assert len(clicks) == 2
    assert errors == []


@pytest.mark.parametrize("action", ["map", "unavailable", "toggle", "clear", "focus"])
def test_idle_discards_pending_clicks(assist, action):
    w, c, clicks, errors = assist
    w.first_resource.setChecked(False)
    c.update(snapshot(1, state=1))
    c.toggle_idle()
    c.update(snapshot(1))
    if action == "map":
        c.update(snapshot(2))
    elif action == "unavailable":
        c.update(snapshot(1, enabled=False))
    elif action == "toggle":
        c.toggle_idle()
    elif action == "clear":
        c.clear()
    else:
        w.overlay.foreground = lambda: 456
        c.idle_pending[1] = 0
    c.idle_next()
    assert clicks == []
    assert not c.idle_pending
    if action != "unavailable":
        assert not c.idle


@pytest.mark.parametrize("completion", ["depleted", "removed", "unavailable"])
def test_idle_collects_existing_resources_serially(assist, monkeypatch, completion):
    w, c, clicks, errors = assist
    w.first_resource.setChecked(False)
    now = [100.0]
    monkeypatch.setattr("voix_des_douze.resource_click.time.monotonic", lambda: now[0])
    monkeypatch.setattr("voix_des_douze.resource_click.random.randint", lambda a, b: 1200)

    def observed(state=0, enabled=True, present=True, new=False):
        resources = ([Resource(1, 1, "Blé", 280, state, enabled)] if present else [])
        resources.append(Resource(2, 1, "Blé", 281, 0, True))
        if new:
            resources.append(Resource(3, 1, "Blé", 282, 0, True))
        return ResourceSnapshot(1, False, tuple(resources), 0, "itw")

    c.update(observed())
    c.toggle_idle()
    c.idle_next()
    assert clicks == []
    now[0] = 101.2
    c.idle_next()
    assert len(clicks) == 1
    assert c.idle_target == 1
    # Repeated states, new arrivals and harvesting in progress must not
    # interrupt the current target, even after all delays have elapsed.
    c.update(observed(new=True))
    now[0] = 110.0
    c.idle_next()
    c.update(observed(state=2, new=True))
    c.idle_next()
    assert len(clicks) == 1
    assert c.idle_target == 1
    c.update(observed(state=1 if completion == "depleted" else 0,
                      enabled=completion != "unavailable",
                      present=completion != "removed", new=True))
    c.idle_next()
    assert len(clicks) == 2
    assert c.idle_target == 2
    c.idle_next()
    assert len(clicks) == 2
    c.stop_idle()
    assert c.idle_target is None
    assert not c.idle_pending
    c.idle_next()
    assert len(clicks) == 2
    assert errors == []


def test_idle_hint_visibility_tracks_mode_and_foreground(assist):
    w, c, clicks, errors = assist
    hint = c.idle_hint
    assert not hint.isVisible()
    c.update(snapshot(1, state=1))
    c.toggle_idle()
    assert hint.isVisible()
    w.overlay.foreground = lambda: 456
    hint.refresh()
    assert not hint.isVisible()
    w.overlay.foreground = lambda: 123
    hint.refresh()
    assert hint.isVisible()
    c.update(snapshot(2, state=1))
    assert not hint.isVisible()
    hint.refresh()
    assert not hint.isVisible()
    c.toggle_idle()
    assert hint.isVisible()
    c.toggle_idle()
    assert not hint.isVisible()


def test_idle_hint_drag_position_survives_restart(assist, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from voix_des_douze.settings import Store

    w, c, clicks, errors = assist
    store = Store(tmp_path / "settings.json")
    store.settings.overlay_screen = w.store.settings.overlay_screen
    w.store = store
    c.update(snapshot(1, state=1))
    c.toggle_idle()
    hint = c.idle_hint
    origin = hint.pos()
    local = QPointF(8, 8)
    start = QPointF(hint.mapToGlobal(QPoint(8, 8)))
    end = start + QPointF(90, 70)
    for kind, position, buttons in (
            (QEvent.MouseButtonPress, start, Qt.LeftButton),
            (QEvent.MouseMove, end, Qt.LeftButton),
            (QEvent.MouseButtonRelease, end, Qt.NoButton)):
        button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
        QApplication.sendEvent(hint, QMouseEvent(
            kind, local, position, button, buttons, Qt.NoModifier))
    expected = hint.bounded_position(origin + QPoint(90, 70))
    assert hint.pos() == expected
    c.stop_idle()
    w.store = Store(store.path)
    hint.move(origin)
    c.toggle_idle()
    assert hint.pos() == expected
    assert hint.isVisible()
    assert errors == []


@pytest.mark.parametrize("other_available", [True, False])
def test_idle_timeout_skips_stuck_target_without_requeuing_it(assist, monkeypatch, other_available):
    w, c, clicks, errors = assist
    now = [100.0]
    monkeypatch.setattr("voix_des_douze.resource_click.time.monotonic", lambda: now[0])
    monkeypatch.setattr("voix_des_douze.resource_click.random.randint", lambda a, b: 1200)
    resources = (Resource(1, 1, "Blé", 280, 0, True),
                 Resource(2, 1, "Blé", 281, 0, True))
    observed = ResourceSnapshot(1, False, resources, 0, "itw")
    c.update(observed)
    c.toggle_idle()
    now[0] = 102.0
    c.idle_next()
    assert c.idle_target == 1
    assert len(clicks) == 1
    if not other_available:
        observed = snapshot(1)
    c.update(observed)
    c.idle_next()
    assert len(clicks) == 1
    now[0] = 112.0
    c.idle_timeout.stop()
    c.idle_expired()
    assert c.idle
    assert len(clicks) == (2 if other_available else 1)
    assert c.idle_target == (2 if other_available else None)
    assert 1 not in c.idle_pending
    c.update(observed)
    c.idle_next()
    assert len(clicks) == (2 if other_available else 1)
    c.stop_idle()
    assert not c.idle_timeout.isActive()
    c.idle_expired()
    assert len(clicks) == (2 if other_available else 1)
    assert errors == []


def test_idle_groups_neighbours_instead_of_following_random_deadlines(assist, monkeypatch):
    w, c, clicks, errors = assist
    now = [100.0]
    monkeypatch.setattr("voix_des_douze.resource_click.time.monotonic", lambda: now[0])
    # Table order and delays both put the far resource before the close pair.
    resources = [Resource(i, 1, "Blé", cell, 0, True)
                 for i, cell in ((1, 280), (2, 293), (3, 284), (4, 281))]

    def observed():
        return ResourceSnapshot(1, False, tuple(resources), 0, "itw")

    c.update(observed())
    c.toggle_idle()
    c.idle_pending.update({1: 101.8, 2: 100.8, 3: 101.0, 4: 103.0})
    now[0] = 102.0
    c.idle_next()
    assert c.idle_target == 1
    resources[0] = Resource(1, 1, "Blé", 280, 1, False)
    c.update(observed())
    c.idle_next()
    assert len(clicks) == 1  # Wait for the near resource, not the ready far one.
    now[0] = 104.0
    c.idle_next()
    assert c.idle_target == 4
    resources[3] = Resource(4, 1, "Blé", 281, 1, False)
    c.update(observed())
    c.idle_next()
    assert c.idle_target == 3
    resources[2] = Resource(3, 1, "Blé", 284, 1, False)
    c.update(observed())
    c.idle_next()
    assert c.idle_target == 2
    assert len(clicks) == 4
    assert errors == []
    c.stop_idle()
    assert c.idle_origin is None


def test_idle_distance_respects_staggered_rows():
    from voix_des_douze.resource_click import idle_nearest

    # Cell 13 is adjacent to 27, despite a larger numeric ID difference.
    assert idle_nearest({1: 14, 2: 13}, 27) == 2
    # The adjacent odd/even-row cell is closer than two horizontal steps.
    assert idle_nearest({1: 282, 2: 294}, 280) == 2
