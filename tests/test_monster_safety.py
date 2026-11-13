"""Network identity, swept ground distance and final-dispatch combat protection."""
import pytest
from PySide6.QtCore import QPointF

from test_network import blob, num, vint
from test_resource_click import assist, click_app, snapshot
from test_idle_route import route, run_next, empty
from voix_des_douze.monster_safety import MonsterSafety, cell_coordinates, path_cells
from voix_des_douze.network_protocol import ProtocolError
from voix_des_douze.overlay import cell_point, fullscreen_transform


def actor(entity, cell, variant=5):
    return (num(1, entity) + blob(2, num(3, cell))
            + blob(3, blob(1, blob(variant, b''))))


def full(safety, map_id=1, actors=()):
    safety.handle('server', 'jnj', num(10, map_id) + b''.join(blob(3, a) for a in actors))


def move(safety, entity, cells, packed=True):
    path = blob(1, b''.join(vint(c) for c in cells)) if packed else b''.join(num(1, c) for c in cells)
    safety.handle('server', 'jnz', path + num(4, entity))


def test_groups_are_distinguished_from_players_npcs_and_reset_per_map():
    safety = MonsterSafety()
    full(safety, actors=(actor(12, 500, 3), actor(-1, 280, 4), actor(-2, 300)))
    sent = []
    safety.send(1, cell_coordinates(280), lambda: sent.append('safe'))
    assert sent == ['safe']
    with pytest.raises(RuntimeError, match='moins de 2'):
        safety.send(1, cell_coordinates(300), lambda: sent.append('unsafe'))
    full(safety, 2)
    safety.send(2, cell_coordinates(300), lambda: sent.append('new map'))
    assert sent == ['safe', 'new map']
    with pytest.raises(RuntimeError, match='non synchronisées'):
        safety.send(1, cell_coordinates(280), lambda: sent.append('old map'))


@pytest.mark.parametrize('packed', [True, False])
def test_movement_reserves_intermediate_cells_and_does_not_guess_animation_end(packed):
    safety = MonsterSafety()
    full(safety, actors=(actor(-20002, 65),))
    move(safety, -20002, [65, 24], packed)
    assert len(path_cells([65, 24])) == 4
    intermediate = path_cells([65, 24]) - {65, 24}
    for cell in intermediate | {65, 24}:
        with pytest.raises(RuntimeError):
            safety.send(1, cell_coordinates(cell), lambda: pytest.fail('unsafe click'))
    move(safety, -20002, [24, 53], packed)
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(65), lambda: pytest.fail('old swept area expired'))


@pytest.mark.parametrize('offset,blocked', [(0, True), (1, True), (1.999, True), (2, False), (3, False)])
def test_two_case_boundary(offset, blocked):
    safety = MonsterSafety()
    full(safety, actors=(actor(-2, 280),))
    x, y = cell_coordinates(280)
    sent = []
    if blocked:
        with pytest.raises(RuntimeError):
            safety.send(1, (x + offset, y), lambda: sent.append(True))
        assert not sent
    else:
        safety.send(1, (x + offset, y), lambda: sent.append(True))
        assert sent == [True]


def test_new_group_and_invalid_updates_fail_closed_until_full_sync():
    safety = MonsterSafety()
    full(safety)
    safety.handle('server', 'jns', blob(2, actor(-2, 280)))
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(280), lambda: pytest.fail('new group ignored'))
    with pytest.raises(ProtocolError):
        move(safety, -2, [281, 282])
    with pytest.raises(RuntimeError, match='non synchronisées'):
        safety.send(1, cell_coordinates(500), lambda: pytest.fail('desync ignored'))
    full(safety)
    safety.handle('client', 'joc', num(2, 2))
    with pytest.raises(RuntimeError, match='non synchronisées'):
        safety.send(1, cell_coordinates(500), lambda: pytest.fail('departure ignored'))


@pytest.mark.parametrize('mode', ['arrival', 'debug', 'idle'])
def test_all_harvest_modes_recheck_monsters_at_dispatch(assist, tmp_path, mode):
    w, c, clicks, errors = assist
    c.update(snapshot(1))
    c.update(snapshot(2))
    if mode == 'debug':
        c.start_debug(tmp_path)
    if mode == 'idle':
        c.toggle_idle()
    # Actor arrives after target scheduling, before timer/shortcut dispatch.
    c.monsters.handle('server', 'jns', blob(1, actor(-2, 280)))
    if mode == 'debug':
        c.manual_click()
    elif mode == 'idle':
        run_next(c)
        assert c.idle and c.idle_skipped == {1}
        assert not c.idle_timeout.isActive()
    else:
        c.timer.stop()
        c.click()
    assert clicks == []
    if mode == 'idle':
        assert errors == []
    else:
        assert any('Protection anti-combat' in error for error in errors)


def test_exit_guard_checks_actual_randomized_point_and_stops_circuit(route, monkeypatch):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    full(c.monsters, actors=(actor(-2, 280),))
    geometry = w.screen().geometry()
    point = fullscreen_transform(geometry, [0, 0, 1]).map(cell_point(280))
    monkeypatch.setattr('voix_des_douze.idle_route.sample_exit', lambda *args: point)
    c.toggle_idle()
    run_next(c)
    assert clicks == []
    assert not c.idle and c.journey.expected is None
    assert not c.journey.timeout.isActive()
    assert any('Protection anti-combat' in error for error in errors)


def test_no_sync_and_no_projection_cannot_send_even_on_empty_resource_map(assist):
    w, c, clicks, errors = assist
    c.update(snapshot(1))
    c.monsters.clear()
    assert not c.click(manual=True)
    assert clicks == []
    full(c.monsters)
    w.store.settings.overlay_adjustments.clear()
    with pytest.raises(RuntimeError, match='repères'):
        c.guarded_send(1, QPointF(1, 1), w.screen().geometry())
    assert clicks == []


def test_player_feet_head_and_movement_are_protected_without_guessing_self_identity():
    safety = MonsterSafety()
    full(safety, actors=(actor(12, 65, 3),))
    x, y = cell_coordinates(65)
    for point in ((x, y), (x - 2, y + 2)):
        with pytest.raises(RuntimeError, match='Protection personnage'):
            safety.send(1, point, lambda: pytest.fail('player menu could open'))
    move(safety, 12, [65, 24])
    for cell in path_cells([65, 24]):
        with pytest.raises(RuntimeError, match='Protection personnage'):
            safety.send(1, cell_coordinates(cell), lambda: pytest.fail('animation path unprotected'))
    sent = []
    safety.send(1, (x + 5, y + 5), lambda: sent.append('clear'))
    full(safety, 2)
    safety.send(2, (x, y), lambda: sent.append('next map'))
    assert sent == ['clear', 'next map']


@pytest.mark.parametrize('variant', [3, 5])
def test_idle_skips_protected_resource_then_harvests_elsewhere_and_leaves(route, variant):
    from voix_des_douze.harvest import Resource, ResourceSnapshot
    w, c, clicks, errors, memory = route
    first = Resource(1, 38, 'Blé', 280, 0, True)
    second = Resource(2, 38, 'Blé', 400, 0, True)
    c.update(ResourceSnapshot(1, True, (first, second), 0, 'jnj'))
    full(c.monsters, actors=(actor(12 if variant == 3 else -2, 280, variant),))
    c.toggle_idle()
    c.idle_origin = 280
    run_next(c)
    assert clicks == []
    assert c.idle and c.idle_target is None and c.idle_skipped == {1}
    assert not c.idle_timeout.isActive()
    run_next(c)
    assert c.idle_target == 2 and len(clicks) == 1
    depleted = Resource(2, 38, 'Blé', 400, 1, True)
    c.update(ResourceSnapshot(1, False, (first, depleted), 0, 'itw'))
    run_next(c)
    assert c.journey.expected == 2 and len(clicks) == 2
    assert errors == []
    for map_id, following in ((2, 3), (3, 4), (4, 3), (3, 2), (2, 1)):
        c.update(empty(map_id))
        run_next(c)
        assert c.journey.expected == following
    # A skipped resource is eligible on the next visit if its protection has cleared.
    c.update(ResourceSnapshot(1, True, (first,), 0, 'jnj'))
    full(c.monsters)
    count = len(clicks)
    run_next(c)
    assert c.idle_target == 1 and len(clicks) == count + 1
    assert not c.idle_skipped


def test_unsynchronized_actor_state_still_stops_idle(route):
    w, c, clicks, errors, memory = route
    c.update(snapshot(1))
    c.monsters.clear()
    c.toggle_idle()
    run_next(c)
    assert not c.idle and clicks == []
    assert 'anti-combat' in errors[-1]


def test_last_skipped_resource_does_not_bypass_a_dangerous_exit(route, monkeypatch):
    w, c, clicks, errors, memory = route
    c.update(snapshot(1, cell=343))
    full(c.monsters, actors=(actor(-2, 356),))
    geometry = w.screen().geometry()
    point = fullscreen_transform(geometry, [0, 0, 1]).map(cell_point(356))
    monkeypatch.setattr('voix_des_douze.idle_route.sample_exit', lambda *args: point)
    c.toggle_idle()
    run_next(c)
    assert c.idle and c.idle_skipped == {1} and clicks == []
    run_next(c)
    assert not c.idle and c.journey.expected is None
    assert not c.journey.timeout.isActive() and clicks == []
    assert errors
