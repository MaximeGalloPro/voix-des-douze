"""Persistent stop diagnostics survive teardown, rotation and storage failures."""
import json

import pytest

from test_idle_route import route, run_next, empty
from test_resource_click import assist, click_app, snapshot
from voix_des_douze.idle_log import IdleJournal


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


@pytest.mark.parametrize('failure', ['focus', 'exit_timeout', 'unexpected', 'clear'])
def test_stop_preserves_inflight_exit_and_cause_before_state_is_cleared(route, failure):
    w, c, clicks, errors, memory = route
    c.update(empty(1))
    c.toggle_idle()
    run_next(c)
    if failure == 'focus':
        w.overlay.foreground = lambda: 456
        c.journey.guard.timeout.emit()
    elif failure == 'exit_timeout':
        c.journey.timeout.timeout.emit()
    elif failure == 'unexpected':
        c.update(empty(4))
    else:
        c.clear('network disconnected')
    assert not c.idle and c.journey.expected is None
    entries = rows(c.idle_journal.path)
    stop = next(row for row in entries if row['événement'] == 'idle arrêté')
    assert stop['contexte']['carte_attendue'] == 2
    assert stop['contexte']['circuit_actif']
    assert stop['contexte']['trajet_restant'] == [2, 3, 4]
    assert stop['détails']['raison']
    if failure == 'focus':
        assert stop['contexte']['pid_premier_plan'] == 456
        assert '456' in stop['détails']['raison']
    elif failure == 'exit_timeout':
        assert '20' in stop['détails']['raison']
    elif failure == 'unexpected':
        assert stop['contexte']['carte'] == 4
        assert '4' in stop['détails']['raison'] and '2' in stop['détails']['raison']
    else:
        assert stop['détails']['raison'] == 'network disconnected'
    c.clear()
    assert rows(c.idle_journal.path) == entries  # Teardown cannot overwrite the original stop.
    assert any(row['événement'] == 'sortie envoyée' for row in entries)


def test_failed_click_keeps_target_resources_and_refusal_in_durable_stop(assist):
    w, c, clicks, errors = assist
    c.update(snapshot(1))
    c.toggle_idle()
    c.idle_pending = {1: 0}
    def refuse(*args):
        raise RuntimeError('AX permission denied')
    c.send = refuse
    c.idle_next()
    stop = rows(c.idle_journal.path)[-1]
    assert stop['événement'] == 'idle arrêté'
    assert 'AX permission denied' in stop['détails']['raison']
    assert stop['contexte']['cible'] == 1
    assert stop['contexte']['ressources'] == [
        {'élément': 1, 'case': 280, 'état': 0, 'récoltable': True}]
    assert c.idle_target is None and not c.idle


def test_rotation_retains_latest_diagnostics_across_logger_recreation(tmp_path):
    errors = []
    for index in range(12):
        journal = IdleJournal(tmp_path, errors.append, max_bytes=600, backups=2)
        journal.write('run', 'stop', {'map': index}, {'reason': 'focus lost'})
    files = sorted(tmp_path.glob('idle.jsonl*'))
    assert len(files) == 3
    assert all(path.stat().st_size <= 600 for path in files)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    assert rows(journal.path)[-1]['contexte']['map'] == 11
    maps = [row['contexte']['map'] for path in files for row in rows(path)]
    assert len(maps) == len(set(maps))
    assert max(maps) == 11 and min(maps) > 0
    assert errors == []


def test_storage_failure_is_reported_and_writing_can_recover(tmp_path):
    directory = tmp_path / 'debug'
    directory.write_text('not a directory')
    errors = []
    journal = IdleJournal(directory, errors.append)
    journal.write('run', 'stop', {}, {'reason': 'first'})
    journal.write('run', 'stop', {}, {'reason': 'second'})
    assert len(errors) == 1
    directory.unlink()
    journal.write('run', 'stop', {}, {'reason': 'recovered'})
    assert rows(journal.path)[-1]['détails']['reason'] == 'recovered'
    assert not journal.failed
