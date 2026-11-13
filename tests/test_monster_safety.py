"""Network identity, swept ground distance and final-dispatch combat protection."""
import pytest
from PySide6.QtCore import QPointF

from test_network import blob, num, vint
from test_resource_click import assist, click_app, snapshot
from test_idle_route import route, run_next, empty
from voix_des_douze.monster_safety import MonsterSafety, PlayerOverlap, cell_coordinates, path_cells
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
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(300), lambda: sent.append('unsafe'))
    full(safety, 2)
    safety.send(2, cell_coordinates(300), lambda: sent.append('new map'))
    assert sent == ['safe', 'new map']
    with pytest.raises(RuntimeError, match='non synchronisées'):
        safety.send(1, cell_coordinates(280), lambda: sent.append('old map'))


@pytest.mark.parametrize('packed', [True, False])
def test_movement_reserves_intermediate_cells_until_next_continuous_movement(packed):
    safety = MonsterSafety()
    full(safety, actors=(actor(-20002, 65),))
    move(safety, -20002, [65, 24], packed)
    assert len(path_cells([65, 24])) == 4
    intermediate = path_cells([65, 24]) - {65, 24}
    for cell in intermediate | {65, 24}:
        with pytest.raises(RuntimeError):
            safety.send(1, cell_coordinates(cell), lambda: pytest.fail('unsafe click'))
    move(safety, -20002, [24, 53], packed)
    sent = []
    safety.send(1, cell_coordinates(65), lambda: sent.append('released'))
    assert sent == ['released']


@pytest.mark.parametrize('offset,blocked', [(0, True), (0.999, True), (1, False), (2, False), (3, False)])
def test_one_case_boundary(offset, blocked):
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


@pytest.mark.parametrize('dx,dy,blocked', [
    (0, -1.5, True), (0, 1.5, True),
    (0.499, -1.999, True), (-0.499, 1.999, True),
    (0.5, 0, False), (-0.5, 0, False),
    (0, -2, False), (0, 2, False),
])
def test_monster_square_keeps_width_and_doubles_height(dx, dy, blocked):
    safety = MonsterSafety()
    full(safety, actors=(actor(-2, 280),))
    u, v = cell_coordinates(280)
    target = (u + dx + dy / 2, v + dx - dy / 2)
    sent = []
    if blocked:
        with pytest.raises(RuntimeError, match='Protection anti-combat'):
            safety.send(1, target, lambda: sent.append(True))
        assert sent == []
    else:
        safety.send(1, target, lambda: sent.append(True))
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


def test_exit_guard_checks_actual_randomized_point_and_waits_for_clearance(route, monkeypatch):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    full(c.monsters, actors=(actor(-2, 280),))
    geometry = w.screen().geometry()
    point = fullscreen_transform(geometry, [0, 0, 1]).map(cell_point(280))
    monkeypatch.setattr('voix_des_douze.idle_route.sample_exit', lambda *args: point)
    c.toggle_idle()
    run_next(c)
    assert clicks == []
    assert c.idle and c.journey.expected is None
    assert not c.journey.timeout.isActive() and c.journey.retry.isActive()
    assert errors == []
    full(c.monsters)
    c.journey.retry.stop()
    c.journey.step()
    assert c.journey.expected == 2 and len(clicks) == 1
    c.journey.step()
    assert len(clicks) == 1


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
    for point in ((x, y), (x - 0.5, y + 0.5)):
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
    full(c.monsters, actors=(actor(-2, 343),))
    geometry = w.screen().geometry()
    point = fullscreen_transform(geometry, [0, 0, 1]).map(cell_point(343))
    monkeypatch.setattr('voix_des_douze.idle_route.sample_exit', lambda *args: point)
    c.toggle_idle()
    run_next(c)
    assert c.idle and c.idle_skipped == {1} and clicks == []
    run_next(c)
    assert c.idle and c.journey.expected is None
    assert c.journey.retry.isActive()
    assert not c.journey.timeout.isActive() and clicks == []
    assert errors == []


def test_player_intermediate_stop_keeps_click_guard_and_accepts_following_path():
    safety = MonsterSafety()
    full(safety, actors=(actor(12, 294, 3),))
    original = [294, 266, 238, 210, 182, 154, 141, 127, 114, 100, 87, 73, 60, 46, 33, 19]
    move(safety, 12, original)
    move(safety, 12, [100])
    assert safety.synchronized
    for cell in (100, 19, 294):
        with pytest.raises(RuntimeError, match='Protection personnage'):
            safety.send(1, cell_coordinates(cell), lambda: pytest.fail('uncertain player area cleared'))
    sent = []
    safety.send(1, cell_coordinates(550), lambda: sent.append('unrelated target'))
    assert sent == ['unrelated target']
    move(safety, 12, [100, 128])
    with pytest.raises(RuntimeError, match='Protection personnage'):
        safety.send(1, cell_coordinates(128), lambda: pytest.fail('following path unprotected'))
    safety.send(1, cell_coordinates(294), lambda: sent.append('old area released'))
    assert sent == ['unrelated target', 'old area released']


def test_player_relocation_protects_old_and_new_areas_until_fresh_state():
    safety = MonsterSafety()
    full(safety, actors=(actor(12, 65, 3),))
    move(safety, 12, [400])
    for cell in (65, 400):
        with pytest.raises(RuntimeError, match='Protection personnage'):
            safety.send(1, cell_coordinates(cell), lambda: pytest.fail('relocation area unprotected'))
    safety.handle('server', 'jns', blob(1, actor(12, 400, 3)))
    sent = []
    safety.send(1, cell_coordinates(65), lambda: sent.append('fresh state'))
    assert sent == ['fresh state']


def test_idle_skips_all_mob_and_uncertain_player_targets_then_leaves(route):
    from voix_des_douze.harvest import Resource, ResourceSnapshot
    w, c, clicks, errors, memory = route
    resources = (Resource(1, 38, 'Blé', 280, 0, True),
                 Resource(2, 38, 'Blé', 400, 0, True))
    c.update(ResourceSnapshot(1, True, resources, 0, 'jnj'))
    full(c.monsters, actors=(actor(-2, 280), actor(12, 398, 3)))
    move(c.monsters, 12, [400])
    c.toggle_idle()
    c.idle_origin = 280
    run_next(c)
    assert c.idle and c.idle_skipped == {1} and clicks == []
    run_next(c)
    assert c.idle and c.idle_skipped == {1, 2} and clicks == []
    run_next(c)
    assert c.idle and c.journey.expected == 2 and len(clicks) == 1
    c.update(empty(2))
    run_next(c)
    assert c.idle and c.journey.expected == 3 and len(clicks) == 2
    assert errors == []


@pytest.mark.parametrize('dx,dy,blocked', [
    (0.5, 0, True), (-0.5, 0, True), (0.501, 0, False),
    (0, -2.75, True), (0, -2.751, False),
    (0, 1.25, True), (0, 1.251, False), (0.75, -4, False),
])
def test_square_player_envelope_boundaries(dx, dy, blocked):
    safety = MonsterSafety()
    full(safety, actors=(actor(12, 280, 3),))
    u, v = cell_coordinates(280)
    target = u + dx + dy / 2, v + dx - dy / 2
    sent = []
    if blocked:
        with pytest.raises(RuntimeError, match='Protection personnage'):
            safety.send(1, target, lambda: sent.append('clicked'))
        assert sent == []
    else:
        safety.send(1, target, lambda: sent.append('clicked'))
        assert sent == ['clicked']


@pytest.mark.parametrize('variant', [3, 5])
def test_passages_fade_in_order_but_stationary_endpoint_does_not(monkeypatch, variant):
    from types import SimpleNamespace
    clock = [100.0]
    monkeypatch.setattr('voix_des_douze.monster_safety.time', SimpleNamespace(monotonic=lambda: clock[0]))
    safety = MonsterSafety()
    full(safety, actors=(actor(12, 294, variant),))
    move(safety, 12, [294, 266, 238, 210, 182, 154, 141, 127, 114, 100, 87, 73, 60, 46, 33, 19])
    opacity = safety.player_opacity if variant == 3 else safety.monster_opacity
    initial = opacity()[12]
    clock[0] = 101.0
    assert 0 < opacity()[12][294] < initial[294]
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(294), lambda: pytest.fail('passage released too soon'))
    clock[0] = 101.41
    sent = []
    safety.send(1, cell_coordinates(294), lambda: sent.append('start released'))
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(100), lambda: pytest.fail('later passage released too soon'))
    clock[0] = 1000.0
    safety.send(1, cell_coordinates(100), lambda: sent.append('middle released'))
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(19), lambda: pytest.fail('stationary endpoint released'))
    assert set(opacity()[12]) == {19}
    assert sent == ['start released', 'middle released']


def test_expired_monster_passage_does_not_release_another_groups_position(monkeypatch):
    from types import SimpleNamespace
    clock = [100.0]
    monkeypatch.setattr('voix_des_douze.monster_safety.time', SimpleNamespace(monotonic=lambda: clock[0]))
    safety = MonsterSafety()
    full(safety, actors=(actor(-2, 294), actor(-3, 294)))
    move(safety, -2, [294, 266, 238])
    clock[0] = 110.0
    with pytest.raises(RuntimeError):
        safety.send(1, cell_coordinates(294), lambda: pytest.fail('stationary group released'))
    assert 294 not in safety.monster_paths[-2]
    assert safety.monster_paths[-3] == {294}
    assert safety.monster_opacity()[-3][294] == 1


def test_blocked_exit_rechecks_fade_without_network_updates_then_posts_once(route, monkeypatch):
    from types import SimpleNamespace
    clock = [100.0]
    monkeypatch.setattr('voix_des_douze.monster_safety.time', SimpleNamespace(monotonic=lambda: clock[0]))
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    full(c.monsters, actors=(actor(12, 294, 3),))
    move(c.monsters, 12, [294, 266, 238, 210, 182, 154, 141, 127, 114, 100, 87, 73, 60, 46, 33, 19])
    point = fullscreen_transform(w.screen().geometry(), [0, 0, 1]).map(cell_point(294))
    monkeypatch.setattr('voix_des_douze.idle_route.sample_exit', lambda *args: point)
    c.toggle_idle()
    run_next(c)
    assert c.idle and c.journey.expected is None and clicks == []
    assert c.journey.retry.isActive() and not c.journey.timeout.isActive()
    # Repeated resource snapshots cannot bypass the obstacle wait.
    c.update(empty(1))
    run_next(c)
    assert clicks == []
    clock[0] = 103.0
    c.journey.retry.stop()
    c.journey.step()
    assert c.journey.expected == 2 and len(clicks) == 1
    assert c.journey.timeout.isActive()
    c.journey.step()
    assert len(clicks) == 1 and errors == []


@pytest.mark.parametrize('failure', ['focus', 'map', 'sync', 'shortcut'])
def test_waiting_exit_cannot_resume_after_loss_of_safety(route, monkeypatch, failure):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    full(c.monsters, actors=(actor(12, 280, 3),))
    point = fullscreen_transform(w.screen().geometry(), [0, 0, 1]).map(cell_point(280))
    monkeypatch.setattr('voix_des_douze.idle_route.sample_exit', lambda *args: point)
    c.toggle_idle()
    run_next(c)
    assert c.journey.retry.isActive() and clicks == []
    if failure == 'focus':
        w.overlay.foreground = lambda: 456
    elif failure == 'map':
        c.update(empty(3))
    elif failure == 'sync':
        c.monsters.clear()
    else:
        c.toggle_idle()
    c.journey.retry.stop()
    c.journey.step()
    assert not c.idle and clicks == []
    assert not c.journey.retry.isActive() and not c.journey.timeout.isActive()
