"""Compare real recommendation inputs with later server snapshots, without game input."""
import json

import pytest

from voix_des_douze.compass_debug import CompassJournal
from voix_des_douze.gathering import GatheringMemory
from voix_des_douze.harvest import Resource, ResourceSnapshot
from voix_des_douze.network_protocol import VERSION


def snapshot(map_id, state=None, enabled=True):
    resources = () if state is None else (Resource(7, 1, 'Frêne', 280, state, enabled),)
    return ResourceSnapshot(map_id, True, resources, 0, 'jnj')


@pytest.fixture
def prediction(tmp_path):
    now = [1000.0]
    catalog = {'game_version': VERSION, 'maps': {
        '1': {'x': 0, 'y': 0, 'world_id': 1},
        '2': {'x': 1, 'y': 0, 'world_id': 1}},
        'respawns': {'1': {'seconds': 60, 'label': 'Estimation de test'}}}
    memory = GatheringMemory(tmp_path / 'memory.sqlite3', catalog, lambda: now[0])
    memory.select_profile('Serveur · personnage')
    memory.observe(snapshot(2, 0))
    memory.observe(snapshot(2, 1, False))
    memory.observe(snapshot(1))
    memory.observe_transition(1, 2)
    errors = []
    journal = CompassJournal(tmp_path / 'debug', errors.append)
    now[0] = 1061.0
    journal.prediction(memory, memory.recommend())
    yield memory, journal, now, errors
    journal.close()
    memory.close()


def records(journal):
    return [json.loads(line) for line in journal.path.read_text().splitlines()]


@pytest.mark.parametrize(('state', 'enabled', 'result'), [
    (1, False, 'prédite prête mais non mature'),
    (0, True, 'prête'),
    (0, False, 'mature mais action indisponible'),
    (2, False, 'récolte en cours'),
    (-1, False, 'état inconnu'),
    (None, False, 'ressource absente'),
])
def test_prediction_is_compared_once_with_arrival(prediction, state, enabled, result):
    memory, journal, now, errors = prediction
    journal.prediction(memory, memory.recommend())
    arrival = snapshot(2, state, enabled)
    journal.arrival(memory.profile, arrival, now[0])
    journal.arrival(memory.profile, arrival, now[0])
    predicted, actual = records(journal)
    assert predicted['échéance'] == 1060
    assert predicted['dernière_observation']['cut'] == 1000
    assert predicted['destination']['source'] == 'Estimation de test'
    assert actual['prédiction'] == predicted['id']
    assert actual['résultat'] == result
    assert actual['attendue_prête_à_arrivée'] is True
    assert actual['écart_échéance_s'] == 1
    assert actual['observation'] == (None if state is None else {
        'element_id': 7, 'type_id': 1, 'name': 'Frêne', 'cell': 280,
        'state': state, 'harvest_enabled': enabled})
    assert errors == []


def test_arrival_before_due_is_not_a_failed_ready_prediction(prediction):
    memory, journal, now, _ = prediction
    journal.reset(memory.profile)
    now[0] = 1050
    journal.prediction(memory, memory.recommend())
    journal.arrival(memory.profile, snapshot(2, 1, False), 1055)
    actual = records(journal)[-1]
    assert actual['résultat'] == 'arrivée avant échéance, non mature'
    assert actual['attendue_prête_à_arrivée'] is False
    assert actual['écart_échéance_s'] == -5


def test_profile_switch_and_stop_revoke_pending_comparisons(prediction):
    memory, journal, now, _ = prediction
    journal.arrival('Autre personnage', snapshot(2, 1, False), now[0])
    assert [r['étape'] for r in records(journal)] == ['prédiction']
    journal.prediction(memory, memory.recommend())
    journal.reset(memory.profile)
    journal.arrival(memory.profile, snapshot(2, 1, False), now[0])
    assert all(r['étape'] == 'prédiction' for r in records(journal))


def test_journal_limit_does_not_break_guidance(prediction):
    memory, journal, now, errors = prediction
    journal.MAX_BYTES = journal.file.tell()
    journal.arrival(memory.profile, snapshot(2, 1, False), now[0])
    assert journal.failed
    assert journal.file is None
    assert len(errors) == 1
    assert memory.recommend().map_id == 2
    journal.prediction(memory, memory.recommend())
    assert len(errors) == 1


def test_last_seen_available_can_be_stale_without_a_respawn_deadline(prediction):
    memory, journal, now, _ = prediction
    memory.observe(snapshot(2, 0))
    memory.observe(snapshot(1))
    journal.reset(memory.profile)
    journal.prediction(memory, memory.recommend())
    journal.arrival(memory.profile, snapshot(2, 1, False), now[0] + 5)
    predicted, actual = records(journal)[-2:]
    assert predicted['échéance'] is None
    assert predicted['destination']['source'] == 'Disponible à la dernière observation'
    assert actual['résultat'] == 'prédite prête mais non mature'
    assert actual['écart_échéance_s'] is None
