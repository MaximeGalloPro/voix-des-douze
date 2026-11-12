import pytest

from voix_des_douze.gathering import MapTransitions
from voix_des_douze.travel import TravelCalibration
from test_network import num

MAPS = {'1': {'x': 0, 'y': 0, 'world_id': 1},
        '2': {'x': 1, 'y': 0, 'world_id': 1},
        '3': {'x': 100, 'y': 0, 'world_id': 1}}


def event(origin, target, arrived):
    return {'origin': origin, 'target': target, 'arrived_at': arrived,
            'requested': target, 'requested_at': arrived - 0.5}


def test_normal_dwell_never_trains_and_pause_discards_idle_interval():
    c = TravelCalibration()
    c.observe(event(1, 2, 10000), MAPS)
    assert c.samples == []
    c.start('Profil')
    c.observe(event(1, 2, 10000), MAPS)  # First arrival is an anchor, not a sample.
    assert c.observe(event(2, 1, 10004), MAPS) == 4
    c.pause()
    c.observe(event(1, 2, 20000), MAPS)
    c.pause()
    assert c.observe(event(2, 1, 30000), MAPS) is None
    assert c.observe(event(1, 2, 30005), MAPS) == 5
    assert c.samples == [4, 5]
    c.cancel()
    c.observe(event(2, 1, 40000), MAPS)
    assert c.samples == [4, 5]


def test_calibration_requires_twenty_samples_and_uses_median():
    c = TravelCalibration()
    c.start('Profil')
    with pytest.raises(ValueError):
        c.finish()
    c.observe(event(1, 2, 100), MAPS)
    now = 100
    for index in range(20):
        now += 4 if index != 5 else 40
        c.observe(event(2, 1, now) if index % 2 == 0 else event(1, 2, now), MAPS)
    assert c.finish() == 4
    assert not c.active


def test_teleport_and_unconfirmed_transition_do_not_become_walk_samples():
    c = TravelCalibration()
    c.start('Profil')
    c.observe(event(1, 2, 100), MAPS)
    assert c.observe(event(2, 3, 105), MAPS) is None
    assert c.observe(event(3, 1, 110), MAPS) is None
    unconfirmed = {**event(1, 2, 115), 'requested': None}
    assert c.observe(unconfirmed, MAPS) is None
    assert c.samples == []


def test_transition_timestamps_measure_request_and_arrival_separately():
    now = [100.0]
    edges, arrivals = [], []
    tracker = MapTransitions(edges.append, arrivals.append, lambda: now[0])
    tracker.handle('server', 'jnj', num(10, 1))
    now[0] = 10000  # Long idle before requesting a change.
    tracker.handle('client', 'joc', num(2, 2))
    now[0] = 10001
    tracker.handle('server', 'jnj', num(10, 2))
    assert edges == [(1, 2)]
    assert arrivals[-1] == {'origin': 1, 'target': 2, 'requested': 2,
                            'requested_at': 10000, 'arrived_at': 10001}
    now[0] = 10002
    tracker.handle('server', 'jnj', num(10, 1))
    assert arrivals[-1]['requested_at'] is None
