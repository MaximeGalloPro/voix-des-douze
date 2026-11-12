"""User interactions: dragging survives refresh/restart; saved profiles remain selectable."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import delete

from voix_des_douze.compass import Compass, CompassSettings
from voix_des_douze.settings import Store


@pytest.fixture(scope="session")
def compass_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def make_compass(compass_app, tmp_path):
    windows = []
    def make():
        window = QWidget()
        window.store = Store(tmp_path / "settings.json")
        window.store.settings.compass_enabled = True
        window.accepting = True
        window.overlay = SimpleNamespace(pid=123, foreground=lambda: 123, isVisible=lambda: False)
        window.errors = []
        window.show_error = window.errors.append
        compass = Compass(window)
        windows.append((window, compass))
        return window, compass
    yield make
    for window, compass in reversed(windows):
        compass.close()
        delete(compass.panel)
        delete(window)


def test_dragged_position_survives_timer_refresh_and_reopen(make_compass):
    window, compass = make_compass()
    compass.refresh()
    panel = compass.panel
    before = panel.pos()
    handle = panel.heading
    start = handle.rect().center()
    delta = QPoint(-120, 70)
    expected = panel.bounded_position(before + delta)
    QTest.mousePress(handle, Qt.LeftButton, pos=start)
    QTest.mouseMove(handle, start + delta)
    QTest.mouseRelease(handle, Qt.LeftButton, pos=start)
    assert panel.pos() == expected
    assert panel.pos() != before
    compass.refresh()
    assert panel.pos() == expected
    reopened_window, reopened = make_compass()
    reopened.refresh()
    assert reopened.panel.pos() == expected
    assert window.errors == []
    # A display resize or stale position must not put the panel off screen.
    reopened_window.store.settings.compass_positions[reopened.panel.placement_key] = [100000, -100000]
    reopened.refresh()
    assert reopened.panel.placement_geometry.contains(reopened.panel.geometry())


def test_profiles_without_observations_survive_restart_and_dropdown_selection(make_compass):
    window, compass = make_compass()
    window.store.settings.compass_profile = "Ancien serveur · personnage"
    compass.memory.select_profile(window.store.settings.compass_profile)
    dialog = CompassSettings(compass)
    dialog.profile.setEditText("Nouveau serveur · personnage")
    dialog.use_profile()
    reopened_window, reopened = make_compass()
    selector = CompassSettings(reopened).profile
    assert {selector.itemText(i) for i in range(selector.count())} == {
        "Ancien serveur · personnage", "Nouveau serveur · personnage"}
    assert selector.currentText() == "Nouveau serveur · personnage"
    selector.setCurrentIndex(selector.findText("Ancien serveur · personnage"))
    selector.activated.emit(selector.currentIndex())
    assert reopened.memory.profile == "Ancien serveur · personnage"
    assert Store(reopened_window.store.path).settings.compass_profile == "Ancien serveur · personnage"


def test_legacy_profile_is_recovered_from_memory_without_settings_history(make_compass):
    window, compass = make_compass()
    compass.memory.select_profile("Ancien profil avec priorités")
    compass.memory.set_preference(38, True, 7)
    compass.memory.select_profile("")
    selector = CompassSettings(compass).profile
    index = selector.findText("Ancien profil avec priorités")
    assert index >= 0
    selector.setCurrentIndex(index)
    selector.activated.emit(index)
    assert compass.memory.profile == "Ancien profil avec priorités"
    assert Store(window.store.path).settings.compass_profiles == ["Ancien profil avec priorités"]
