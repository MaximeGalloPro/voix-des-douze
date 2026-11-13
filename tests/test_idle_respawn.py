"""Continuous respawn evidence excludes timing artifacts and interrupted cycles."""
import pytest

from test_gathering import memory, resource, snapshot


def test_respawn_uses_elapsed_time_not_wall_clock(memory):
    m, wall, _ = memory
    elapsed = [10.0]
    m.duration_clock = lambda: elapsed[0]
    m.observe(snapshot(1, resource()))
    m.observe(snapshot(1, resource(state=1)))
    wall[0] -= 3600
    elapsed[0] += 75.25
    measured = m.observe(snapshot(1, resource()))
    assert [(r.element_id, seconds) for r, seconds in measured] == [(10, 75.25)]
    assert m.estimate(38)[0] == 75.25
    assert m.observe(snapshot(1, resource())) == []


@pytest.mark.parametrize('interrupt', ['unknown', 'absent', 'map', 'session', 'profile'])
def test_interrupted_cycle_never_becomes_a_respawn_sample(memory, interrupt):
    m, now, _ = memory
    m.observe(snapshot(1, resource()))
    m.observe(snapshot(1, resource(state=1)))
    if interrupt == 'unknown':
        m.observe(snapshot(1, resource(state=-1)))
    elif interrupt == 'absent':
        m.observe(snapshot(1))
    elif interrupt == 'map':
        m.observe(snapshot(2))
    elif interrupt == 'session':
        m.reset_session()
    else:
        m.select_profile('autre personnage')
    now[0] += 100
    m.observe(snapshot(1, resource(state=1)))
    now[0] += 50
    assert m.observe(snapshot(1, resource())) == []
    assert m.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 0
