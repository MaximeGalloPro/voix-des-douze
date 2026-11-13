"""User interactions: dragging survives refresh/restart; saved profiles remain selectable."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QWidget
from shiboken6 import delete

from voix_des_douze.compass import Compass, CompassSettings
from voix_des_douze.settings import Store
from voix_des_douze.harvest import Resource, ResourceSnapshot
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsSimpleTextItem
from voix_des_douze.resource_click import FirstResourceClick
from voix_des_douze.ui import MainWindow


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
        window.resource_click = FirstResourceClick(window, send=lambda *args: None)
        window.idle_route_status = QLabel(window)
        window.idle_route_selector = QComboBox(window)
        window.refresh_idle_route_status = lambda: MainWindow.refresh_idle_route_status(window)
        compass = Compass(window)
        windows.append((window, compass))
        return window, compass
    yield make
    for window, compass in reversed(windows):
        window.resource_click.stop_idle()
        compass.close()
        delete(compass.panel)
        delete(window)


def test_dragged_position_survives_timer_refresh_and_reopen(make_compass):
    window, compass = make_compass()
    compass.refresh()
    panel = compass.panel
    before = panel.pos()
    QTest.mouseMove(panel, panel.rect().center())
    handle = panel.move_handle
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


def test_hover_reveals_handle_but_arrow_cannot_move_panel(make_compass):
    _, compass = make_compass()
    compass.refresh()
    panel = compass.panel
    QApplication.sendEvent(panel, QEvent(QEvent.Leave))
    assert not panel.move_handle.isVisible()
    before = panel.pos()
    QTest.mousePress(panel.arrow, Qt.LeftButton)
    QTest.mouseMove(panel.arrow, QPoint(10, 10))
    QTest.mouseRelease(panel.arrow, Qt.LeftButton)
    assert panel.pos() == before
    QApplication.sendEvent(panel, QEvent(QEvent.Enter))
    assert panel.move_handle.isVisible()
    QApplication.sendEvent(panel, QEvent(QEvent.Leave))
    assert not panel.move_handle.isVisible()


def test_hover_without_native_enter_events_and_after_hiding(make_compass, monkeypatch):
    from PySide6.QtGui import QCursor
    _, compass = make_compass()
    cursor = QPoint(-10000, -10000)
    monkeypatch.setattr(QCursor, "pos", lambda: cursor)
    compass.refresh()
    panel = compass.panel
    assert not panel.move_handle.isVisible()
    # No enter event: macOS can pass fully transparent pixels through.
    cursor = panel.mapToGlobal(QPoint(panel.width() - 2, 2))
    QTest.qWait(180)
    assert panel.move_handle.isVisible()
    cursor = QPoint(-10000, -10000)
    QTest.qWait(180)
    assert not panel.move_handle.isVisible()
    panel.drag_offset = QPoint(1, 1)
    QTest.qWait(180)
    assert panel.move_handle.isVisible()
    panel.hide()
    assert not panel.hover_timer.isActive()
    assert not panel.move_handle.isVisible()
    panel.drag_offset = None
    cursor = panel.mapToGlobal(QPoint(panel.width() - 2, 2))
    compass.refresh()
    QTest.qWait(180)
    assert panel.move_handle.isVisible()


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


def test_map_counts_hover_states_and_live_removal(make_compass):
    _, compass = make_compass()
    memory = compass.memory
    memory.catalog = {"maps": {"1": {"world_id": 1, "x": -2, "y": 3},
                               "2": {"world_id": 1, "x": -1, "y": 3}},
                      "respawns": {}, "game_version": memory.catalog["game_version"]}
    memory.select_profile("Test")
    memory.observe(ResourceSnapshot(1, True, (
        Resource(1, 38, "Blé", 1, 0, True),
        Resource(2, 38, "Blé", 2, 1, False),
        Resource(3, 39, "Orge", 3, 0, False)), 0, "jnj"))
    memory.observe(ResourceSnapshot(2, True, (), 0, "jnj"))
    memory.observe_transition(1, 2)
    settings = CompassSettings(compass)
    settings.map_button.click()
    dialog = compass.map_dialog
    assert dialog.isVisible()
    tiles = [item for item in dialog.scene.items() if isinstance(item, QGraphicsRectItem)]
    assert len(tiles) == 2
    tooltip = next(item.toolTip() for item in tiles if "Blé" in item.toolTip())
    assert "3 ressource(s)" in tooltip
    assert "Blé × 1 — mature · action disponible" in tooltip
    assert "Blé × 1 — non mature" in tooltip
    assert "Orge × 1 — mature · action indisponible" in tooltip
    assert "dernière observation · à vérifier" in tooltip
    assert {item.text() for item in dialog.scene.items() if isinstance(item, QGraphicsSimpleTextItem)} >= {"3", "·", "-2,3"}
    memory.observe(ResourceSnapshot(1, True, (Resource(1, 38, "Blé", 1, 2, False),), 0, "jnj"))
    QTest.qWait(1100)  # Timer refresh, without reopening the window.
    tooltips = [item.toolTip() for item in dialog.scene.items() if isinstance(item, QGraphicsRectItem)]
    assert any("1 ressource(s)" in text and "récolte en cours" in text for text in tooltips)
    assert not any("Orge" in text for text in tooltips)
    scale = dialog.view.transform().m11()
    dialog.zoom(1.25)
    assert dialog.view.transform().m11() == pytest.approx(scale * 1.25)


def test_map_separates_worlds_and_profiles_with_shared_coordinates(make_compass):
    _, compass = make_compass()
    memory = compass.memory
    memory.catalog["maps"] = {"1": {"world_id": 1, "x": 0, "y": 0},
                              "2": {"world_id": 2, "x": 0, "y": 0},
                              "3": {"world_id": 1, "x": 0, "y": 0}}
    memory.select_profile("A")
    for map_id, name in ((1, "Blé"), (2, "Orge"), (3, "Sauge")):
        memory.observe(ResourceSnapshot(map_id, True, (Resource(1, 38, name, 1, 0, True),), 0, "jnj"))
    compass.show_map()
    dialog = compass.map_dialog
    tooltips = [item.toolTip() for item in dialog.scene.items() if isinstance(item, QGraphicsRectItem)]
    assert len(tooltips) == 1
    assert "Blé" in tooltips[0] and "Sauge" in tooltips[0] and "Orge" not in tooltips[0]
    assert "2 ressource(s)" in tooltips[0]  # Same-world coordinate collisions are aggregated.
    dialog.world.setCurrentIndex(dialog.world.findData(2))
    tooltips = [item.toolTip() for item in dialog.scene.items() if isinstance(item, QGraphicsRectItem)]
    assert len(tooltips) == 1 and "Orge" in tooltips[0] and "Blé" not in tooltips[0]
    memory.select_profile("B")
    QTest.qWait(1100)
    assert dialog.scene.items() == []
    assert dialog.empty.text() == "Aucune carte observée pour ce profil."
    memory.select_profile("A")
    dialog.close()
    compass.show_map()
    assert compass.map_dialog is dialog
    assert dialog.scene.items()


def test_map_keeps_empty_disconnected_visits_after_restart(make_compass):
    _, compass = make_compass()
    catalog = {"maps": {"1": {"world_id": 1, "x": 0, "y": 0},
                        "2": {"world_id": 1, "x": 20, "y": 20}},
               "respawns": {}, "game_version": compass.memory.catalog["game_version"]}
    compass.memory.catalog = catalog
    compass.memory.select_profile("Exploration vide")
    for map_id in (1, 2, 999):
        compass.memory.observe(ResourceSnapshot(map_id, True, (), 0, "jnj"))
    _, reopened = make_compass()
    reopened.memory.catalog = catalog
    assert "Exploration vide" in reopened.memory.profiles()
    reopened.memory.select_profile("Exploration vide")
    reopened.show_map()
    dialog = reopened.map_dialog
    tiles = [item for item in dialog.scene.items() if isinstance(item, QGraphicsRectItem)]
    assert len(tiles) == 2
    assert all("0 ressource(s)" in item.toolTip() for item in tiles)
    assert dialog.empty.text() == "1 carte(s) sans coordonnées connues."
