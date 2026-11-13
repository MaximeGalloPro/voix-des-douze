"""Ordered distant stops, confirmed hops, no game input, profile isolation."""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QPushButton

from test_resource_click import assist, click_app, snapshot
from voix_des_douze.gathering import GatheringMemory
from voix_des_douze.harvest import Resource, ResourceSnapshot
from voix_des_douze.idle_route import RouteEditor, WalkingGraph
from voix_des_douze.network_protocol import VERSION
from voix_des_douze.settings import Store


@pytest.fixture
def route(assist, tmp_path):
    w, c, clicks, errors = assist
    w.resource_click = c
    store = Store(tmp_path / 'settings.json')
    store.settings = w.store.settings
    w.store = store
    s = store.settings
    s.compass_profile = 'Serveur / Personnage'
    width, height = w.screen().geometry().width(), w.screen().geometry().height()
    s.map_exit_screen = s.overlay_screen
    s.map_exits[s.map_exit_screen] = {
        'size': [width, height], 'radius': [10, 10],
        'points': {'left': [10, height // 2], 'right': [width - 11, height // 2],
                   'up': [width // 2, 10], 'down': [width // 2, height - 11]}}
    maps = {str(i): {'world_id': 1, 'x': i - 1, 'y': 0} for i in range(1, 5)}
    maps['9'] = {'world_id': 2, 'x': 0, 'y': 0}
    memory = GatheringMemory(tmp_path / 'memory.db', {'game_version': VERSION, 'maps': maps})
    memory.select_profile(s.compass_profile)
    for a, b in ((1, 2), (2, 3), (3, 4), (4, 3), (3, 2), (2, 1)):
        memory.observe_transition(a, b)
    w.compass = SimpleNamespace(memory=memory)
    s.idle_routes[s.compass_profile] = {'selected': 'Initial', 'circuits': {
        'Initial': {'enabled': True, 'maps': [1, 4]}}}
    yield w, c, clicks, errors, memory
    c.stop_idle()
    memory.close()


def empty(map_id):
    return ResourceSnapshot(map_id, True, (), 0, 'jnj')


def run_next(c):
    c.idle_timer.stop()
    c.idle_pending = dict.fromkeys(c.idle_pending, 0)
    c.idle_next()


def test_distant_loop_harvests_stops_but_not_intermediate_maps(route):
    w, c, clicks, errors, memory = route
    c.update(snapshot(1))
    c.toggle_idle()
    run_next(c)
    assert c.idle_target == 1
    assert c.journey.expected is None
    c.update(snapshot(1, state=2))
    run_next(c)
    assert len(clicks) == 1  # Do not leave while harvesting.
    c.update(snapshot(1, state=1))
    run_next(c)
    assert c.journey.expected == 2
    assert len(clicks) == 2
    c.update(snapshot(1))  # Repopulation after exit click cannot race a second click.
    run_next(c)
    assert len(clicks) == 2
    for map_id, following in ((2, 3), (3, 4)):
        c.update(snapshot(map_id))  # Harvestable, but not a configured stop.
        assert c.idle and c.idle_target is None
        assert not c.timer.isActive()
        run_next(c)
        assert c.journey.expected == following
    c.update(snapshot(4))
    assert c.journey.at_stop
    run_next(c)
    assert c.idle_target == 1
    c.update(snapshot(4, state=1))
    run_next(c)
    assert c.journey.expected == 3
    for map_id, following in ((3, 2), (2, 1)):
        c.update(empty(map_id))
        run_next(c)
        assert c.journey.expected == following
    c.update(empty(1))
    assert c.journey.index == 0
    run_next(c)
    assert c.journey.expected == 2  # Second lap, no restart required.
    assert errors == []


def test_starts_outside_stops_and_repeated_states_do_not_post_twice(route):
    w, c, clicks, errors, memory = route
    c.update(snapshot(2))
    c.toggle_idle()
    run_next(c)
    assert c.journey.expected == 1
    assert c.idle_target is None  # No harvesting until the first configured stop.
    for _ in range(3):
        c.update(snapshot(2))
        run_next(c)
    assert len(clicks) == 1
    c.update(empty(1))
    run_next(c)
    assert c.journey.expected == 2
    assert errors == []



@pytest.mark.parametrize('start_map', [5, 9])
def test_outside_circuit_stays_local_even_with_a_path_to_the_run(route, start_map):
    w, c, clicks, errors, memory = route
    memory.catalog['maps']['5'] = {'world_id': 1, 'x': 0, 'y': 1}
    memory.observe_transition(5, 1)
    c.update(snapshot(start_map))
    c.toggle_idle()
    run_next(c)
    assert c.idle and not c.journey.active
    assert c.idle_target == 1
    c.update(snapshot(start_map, state=1))
    run_next(c)
    assert c.idle and c.idle_target is None
    assert len(clicks) == 1
    assert c.journey.expected is None
    assert errors == []

@pytest.mark.parametrize('failure', ['timeout', 'unexpected', 'focus', 'pause', 'clear', 'calibration', 'profile', 'shortcut'])
def test_inflight_exit_stops_without_retry_or_arrival_click(route, failure):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    c.toggle_idle()
    run_next(c)
    assert c.journey.expected == 2
    if failure == 'timeout':
        c.journey.timeout.timeout.emit()
    elif failure == 'unexpected':
        c.update(empty(4))
    elif failure == 'clear':
        c.clear()
    elif failure == 'shortcut':
        c.toggle_idle()
    else:
        if failure == 'focus':
            w.overlay.foreground = lambda: 456
        elif failure == 'pause':
            w.paused = True
        elif failure == 'calibration':
            w.overlay.calibrating = True
        elif failure == 'profile':
            w.store.settings.compass_profile = 'Autre personnage'
        c.journey.check()
    assert not c.idle
    assert not c.journey.timeout.isActive()
    assert not c.journey.guard.isActive()
    assert not c.timer.isActive()
    run_next(c)
    assert len(clicks) == 1


@pytest.mark.parametrize('failure', ['missing_return', 'other_profile', 'size', 'other_screen', 'no_stops'])
def test_circuit_start_rejects_unreachable_or_uncalibrated_routes(route, failure):
    w, c, clicks, errors, memory = route
    s = w.store.settings
    if failure == 'missing_return':
        memory.db.execute('DELETE FROM edges WHERE origin=4')
    elif failure == 'other_profile':
        memory.select_profile('Autre personnage')
    elif failure == 'size':
        s.map_exits[s.map_exit_screen]['size'][0] += 1
    elif failure == 'other_screen':
        s.map_exit_screen = 'absent'
    else:
        s.idle_routes[s.compass_profile]['circuits']['Initial']['maps'] = []
    c.update(empty(1))
    c.toggle_idle()
    run_next(c)
    assert not c.idle
    assert clicks == []
    assert errors


def test_graph_uses_directed_profile_scoped_walks_only(route):
    w, c, clicks, errors, memory = route
    graph = WalkingGraph(memory)
    assert graph.path(1, 4) == (2, 3, 4)
    memory.db.execute('DELETE FROM edges WHERE origin=4')
    with pytest.raises(ValueError, match='4 → 1'):
        WalkingGraph(memory).loop((1, 4))
    # Stored corrupt/stale teleports aren't usable as calibrated exit directions.
    memory.db.execute('INSERT INTO edges VALUES (?,?,?)', (memory.profile, 4, 9))
    with pytest.raises(ValueError):
        WalkingGraph(memory).path(4, 9)
    memory.select_profile('Autre personnage')
    with pytest.raises(ValueError):
        WalkingGraph(memory).path(1, 4)


@pytest.mark.parametrize('state', [0, 2])
def test_last_failed_harvest_continues_loop_and_is_retried_next_visit(route, state):
    w, c, clicks, errors, memory = route
    c.update(snapshot(1))
    c.toggle_idle()
    run_next(c)
    c.update(snapshot(1, state=state))
    c.idle_expired()
    assert c.idle and c.journey.active
    assert c.journey.expected == 2
    assert c.idle_skipped == {1}
    assert len(clicks) == 2  # One harvest attempt, then the exit.
    c.update(snapshot(1))
    run_next(c)
    assert len(clicks) == 2  # Repeated availability cannot retry the failed target.
    for map_id, following in ((2, 3), (3, 4), (4, 3), (3, 2), (2, 1)):
        c.update(empty(map_id))
        run_next(c)
        assert c.journey.expected == following
    c.update(snapshot(1))
    run_next(c)
    assert c.idle_target == 1
    assert not c.idle_skipped
    assert errors == []


def test_failed_harvest_is_ignored_until_departure_even_after_availability_changes(route):
    w, c, clicks, errors, memory = route
    first = snapshot(1).resources[0]
    second = Resource(2, 38, 'Blé', 281, 0, True)
    c.update(ResourceSnapshot(1, True, (first, second), 0, 'jnj'))
    c.toggle_idle()
    run_next(c)
    failed = c.idle_target
    other = 2 if failed == 1 else 1
    c.idle_pending = dict.fromkeys(c.idle_pending, 0)
    c.idle_expired()
    assert c.idle_target == other
    assert len(clicks) == 2
    assert c.journey.expected is None
    # A later disappearance and respawn must not undo the visit's skip.
    remaining = second if other == 2 else first
    c.update(ResourceSnapshot(1, False, (remaining,), 0, 'itw'))
    c.update(ResourceSnapshot(1, False, (first, second), 0, 'itw'))
    assert failed not in c.idle_pending
    depleted = Resource(other, 38, 'Blé', remaining.cell, 1, True)
    blocked = first if failed == 1 else second
    c.update(ResourceSnapshot(1, False, (blocked, depleted), 0, 'itw'))
    run_next(c)
    assert c.journey.expected == 2
    assert len(clicks) == 3  # Both harvest attempts, then departure.
    assert errors == []


def test_recording_starts_at_next_map_and_finishes_with_name_per_profile(route):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    w.store.settings.idle_routes['Autre personnage'] = {
        'selected': 'Autre', 'circuits': {'Autre': {'enabled': False, 'maps': [9]}}}
    dialog = RouteEditor(w)
    dialog.name.setText('Boucle des champs')
    dialog.show()
    QTest.mouseClick(dialog.start_button, Qt.LeftButton)
    dialog.observe(empty(1))
    assert dialog.stops == []
    for map_id in (2, 2, 3, 4, 1, 2):
        dialog.observe(empty(map_id))
    assert dialog.stops == [2, 3, 4, 1, 2]
    QTest.mouseClick(dialog.finish_button, Qt.LeftButton)
    assert not dialog.recording
    assert dialog.result() == QDialog.Accepted
    dialog.observe(empty(3))
    restored = Store(w.store.path).settings.idle_routes
    assert restored[memory.profile] == {'selected': 'Boucle des champs', 'circuits': {
        'Initial': {'enabled': True, 'maps': [1, 4]},
        'Boucle des champs': {'enabled': True, 'maps': [2, 3, 4, 1]}}}
    assert restored['Autre personnage']['circuits']['Autre'] == {'enabled': False, 'maps': [9]}
    reopened = RouteEditor(w)
    assert reopened.name.text() == 'Boucle des champs'
    reopened.start_recording()
    reopened.observe(empty(3))
    reopened.reject()
    assert Store(w.store.path).settings.idle_routes == restored
    assert clicks == []
    dialog.close()
    reopened.close()


def test_recording_requires_name_and_stops_on_interruption(route):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    dialog = RouteEditor(w)
    dialog.name.clear()
    dialog.start_recording()
    assert not dialog.recording
    dialog.name.setText('Test')
    dialog.start_recording()
    dialog.observe(empty(2))
    dialog.interrupt()
    dialog.observe(empty(3))
    assert dialog.stops == [2]
    assert not dialog.finish_button.isEnabled()
    dialog.close()


def test_editor_blocks_enabled_loop_without_return_and_rolls_back_save_failure(route, monkeypatch):
    w, c, clicks, errors, memory = route
    original = dict(w.store.settings.idle_routes[memory.profile])
    memory.db.execute('DELETE FROM edges WHERE origin=4')
    dialog = RouteEditor(w)
    dialog.name.setText('Essai')
    dialog.save()
    assert dialog.result() == QDialog.Rejected
    assert '4 → 1' in dialog.notice.text()
    assert not dialog.recording
    dialog.enabled.setChecked(False)
    monkeypatch.setattr(w.store, 'save', lambda: (_ for _ in ()).throw(OSError('disque plein')))
    dialog.save()
    assert 'disque plein' in dialog.notice.text()
    assert w.store.settings.idle_routes[memory.profile] == original
    dialog.close()


@pytest.mark.parametrize('configuration', [
    {'enabled': True, 'maps': [1]}, {'enabled': True, 'maps': [1, 1]},
    {'enabled': True, 'maps': [True, 4]}, {'enabled': True, 'maps': [1, -4]},
    {'enabled': 'oui', 'maps': [1, 4]}, {'enabled': False, 'maps': '1,4'},
])
def test_invalid_persisted_routes_are_rejected(tmp_path, configuration):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'idle_routes': {'personnage': configuration}}))
    with pytest.raises(ValueError, match='Circuit de cartes idle invalide'):
        Store(path)


def test_exit_clicks_follow_all_four_calibrated_directions(route):
    w, c, clicks, errors, memory = route
    for map_id, (x, y) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1)), start=1):
        memory.catalog['maps'][str(map_id)].update(x=x, y=y)
    memory.observe_transition(4, 1)
    w.store.settings.idle_routes[memory.profile]['circuits']['Initial']['maps'] = [1, 2, 3, 4]
    c.update(empty(1))
    c.toggle_idle()
    for following, direction in ((2, 'right'), (3, 'down'), (4, 'left'), (1, 'up')):
        run_next(c)
        assert c.journey.expected == following
        pid, global_point, geometry = clicks[-1]
        local = global_point - geometry.topLeft()
        profile = w.store.settings.map_exits[w.store.settings.map_exit_screen]
        x, y = profile['points'][direction]
        if direction in ('left', 'right'):
            assert local.x() == x
            assert y - profile['radius'][1] <= local.y() <= y + profile['radius'][1]
        else:
            assert local.y() == y
            assert x - profile['radius'][0] <= local.x() <= x + profile['radius'][0]
        assert pid == w.overlay.pid
        assert geometry.contains(global_point.toPoint())
        c.update(empty(following))
    assert errors == []


def test_named_library_selection_persists_and_drives_idle(route):
    from PySide6.QtWidgets import QComboBox, QLabel
    from voix_des_douze.ui import MainWindow
    w, c, clicks, errors, memory = route
    settings = w.store.settings
    profile = memory.profile
    settings.idle_routes[profile]['circuits']['Retour'] = {'enabled': True, 'maps': [4, 1]}
    w.idle_route_selector = QComboBox(w)
    w.idle_route_status = QLabel(w)
    w.refresh_idle_route_status = lambda: MainWindow.refresh_idle_route_status(w)
    w.idle_route_selector.currentTextChanged.connect(lambda name: MainWindow.select_idle_route(w, name))
    w.refresh_idle_route_status()
    c.update(empty(1))
    c.toggle_idle()
    assert c.idle
    w.idle_route_selector.setCurrentText('Retour')
    assert not c.idle
    restored = Store(w.store.path).settings.idle_routes[profile]
    assert restored['selected'] == 'Retour'
    assert restored['circuits']['Initial']['maps'] == [1, 4]
    c.toggle_idle()
    assert c.journey.stops == (4, 1)
    assert c.idle
    assert clicks == []


def test_legacy_named_circuit_is_migrated_without_loss(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'idle_routes': {
        'Personnage': {'name': 'Champs', 'enabled': True, 'maps': [1, 2, 1, 3]}}}))
    expected = {'selected': 'Champs', 'circuits': {
        'Champs': {'enabled': True, 'maps': [1, 2, 1, 3]}}}
    assert Store(path).settings.idle_routes['Personnage'] == expected
    assert Store(path).settings.idle_routes['Personnage'] == expected
