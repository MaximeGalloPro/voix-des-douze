"""Consumer-visible respawn estimates, observed routes and profile isolation."""
import pytest

from voix_des_douze.gathering import GatheringMemory, MapTransitions
from voix_des_douze.harvest import Resource, ResourceSnapshot
from voix_des_douze.network_protocol import VERSION
from test_network import num


@pytest.fixture
def memory(tmp_path):
    now = [1000.0]
    catalog = {"game_version": VERSION, "maps": {
        "1": {"x": 0, "y": 0, "world_id": 1},
        "2": {"x": 1, "y": 0, "world_id": 1},
        "3": {"x": 2, "y": 0, "world_id": 1},
        "4": {"x": 0, "y": 1, "world_id": 2},
    }, "respawns": {"38": {"seconds": 600, "label": "Estimation historique"}}}
    m = GatheringMemory(tmp_path / "memory.sqlite3", catalog, lambda: now[0])
    m.select_profile("Serveur · personnage")
    yield m, now, catalog
    m.close()


def resource(element=10, type_id=38, state=0, enabled=True):
    return Resource(element, type_id, str(type_id), 280, state, enabled)


def snapshot(map_id=1, *resources):
    return ResourceSnapshot(map_id, True, resources, 0, "itw")


def test_timer_starts_at_observed_depletion_not_harvest_start_or_duplicate_snapshot(memory):
    m, now, _ = memory
    m.observe(snapshot(1, resource()))
    now[0] += 2
    m.observe(snapshot(1, resource(state=2, enabled=False)))
    assert m.recommend() is None
    now[0] += 3
    m.observe(snapshot(1, resource(state=1, enabled=False)))
    assert m.recommend().wait == 600
    now[0] += 20
    m.observe(snapshot(1, resource(state=1, enabled=False)))
    assert m.recommend().wait == 580
    assert m.db.execute("SELECT COUNT(*) FROM harvests").fetchone()[0] == 1
    now[0] += 600
    assert m.recommend().wait == 0
    assert "estimée" in m.recommend().certainty


def test_first_seen_depleted_is_not_a_known_cut_and_unknown_types_have_no_fake_timer(memory):
    m, now, _ = memory
    m.observe(snapshot(1, resource(state=1)))
    assert m.recommend() is None
    m.observe(snapshot(1, resource(type_id=99)))
    now[0] += 1
    m.observe(snapshot(1, resource(type_id=99, state=1)))
    assert m.recommend() is None
    assert m.estimate(99)[0] is None


def test_continuous_regrowth_teaches_but_return_from_another_map_does_not(memory):
    m, now, _ = memory
    m.observe(snapshot(1, resource(type_id=99)))
    now[0] += 2
    m.observe(snapshot(1, resource(type_id=99, state=1)))
    now[0] += 75
    m.observe(snapshot(1, resource(type_id=99)))
    assert m.estimate(99)[0] == 75
    m.observe(snapshot(1, resource(type_id=99, state=1)))
    m.observe(snapshot(2))
    now[0] += 500
    m.observe(snapshot(1, resource(type_id=99)))
    assert m.estimate(99)[0] == 75
    m.observe(snapshot(1, resource(type_id=99, state=1)))
    m.reset_session()
    now[0] += 500
    m.observe(snapshot(1, resource(type_id=99)))
    assert m.estimate(99)[0] == 75


def test_available_first_then_priority_and_distance_with_directed_known_paths(memory):
    m, _, _ = memory
    m.observe(snapshot(3, resource(30)))
    m.observe(snapshot(2, resource(20, 99)))
    m.observe(snapshot(1, resource(10)))
    m.observe_transition(1, 2)
    m.observe_transition(2, 3)
    m.set_preference(38, True, 1)
    m.set_preference(99, True, 2)
    assert m.recommend().element_id == 10
    m.observe(snapshot(1, resource(10, state=1)))
    # Ready remote resource wins over waiting high priority at home.
    assert m.recommend().element_id == 30
    assert m.recommend().direction == "Est →"
    assert m.recommend().steps == 2
    assert m.recommend().command == "/travel 2,0"
    m.set_preference(38, False, 1)
    assert m.recommend().element_id == 20
    assert "à vérifier" in m.recommend().certainty
    m.observe(snapshot(3, resource(30)))
    assert m.recommend() is None  # No invented return path to map 2.


def test_if_nothing_ready_earliest_estimate_wins_over_priority(memory):
    m, now, catalog = memory
    catalog["respawns"]["99"] = {"seconds": 60, "label": "Test measured estimate"}
    m.observe(snapshot(2, resource(20, 99)))
    now[0] += 1
    m.observe(snapshot(2, resource(20, 99, state=1)))
    m.observe(snapshot(1, resource()))
    now[0] += 1
    m.observe(snapshot(1, resource(state=1)))
    m.observe_transition(1, 2)
    m.set_preference(99, True, 99)
    m.set_preference(38, True, 1)
    assert m.recommend().element_id == 20
    assert m.recommend().wait == 59


def test_teleports_other_worlds_and_unreachable_resources_are_not_walk_paths(memory):
    m, _, _ = memory
    m.observe(snapshot(3, resource()))
    m.observe(snapshot(4, resource()))
    m.observe(snapshot(1))
    m.observe_transition(1, 3)
    m.observe_transition(1, 4)
    assert m.recommend() is None
    m.observe_transition(1, 2)
    m.observe_transition(2, 3)
    assert m.recommend().map_id == 3


def test_profiles_and_restart_preserve_memory_but_not_live_confirmation(memory):
    m, _, _ = memory
    m.observe(snapshot(2, resource()))
    m.observe_transition(1, 2)
    m.set_preference(38, True, 7)
    m.select_profile("Autre serveur")
    m.observe(snapshot(1))
    assert m.recommend() is None
    assert m.types() == []
    m.select_profile("Serveur · personnage")
    m.observe(snapshot(1))
    assert m.recommend().map_id == 2
    assert m.types()[0]["priority"] == 7
    assert "à vérifier" in m.recommend().certainty
    path = m.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = GatheringMemory(path, m.catalog, m.clock)
    try:
        reopened.select_profile("Serveur · personnage")
        assert reopened.recommend() is None  # No current-map guess after restart.
        reopened.observe(snapshot(1))
        assert reopened.recommend().map_id == 2
        assert reopened.types()[0]["priority"] == 7
    finally:
        reopened.close()


def test_map_edge_requires_request_and_matching_server_confirmation():
    edges = []
    tracker = MapTransitions(edges.append)
    tracker.handle("server", "jnj", num(10, 1))
    tracker.handle("client", "joc", num(1, 1)+num(2, 2))
    assert edges == []
    tracker.handle("server", "jnj", num(10, 3))
    assert edges == []
    tracker.handle("client", "joc", num(2, 2))
    tracker.handle("server", "jnj", num(10, 2))
    assert edges == [(3, 2)]
    tracker.handle("server", "jnj", num(10, 1))
    assert edges == [(3, 2)]
