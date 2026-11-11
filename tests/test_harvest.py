"""Observed resource states, map boundaries and alert eligibility."""
from voix_des_douze.harvest import HarvestTracker
from test_network import blob, num


def stated(element_id, cell, state=0, current=True):
    # Zero state is omitted on the observed wire.
    return num(3, element_id) + num(4, cell) + (num(5, state) if state else b"") + (num(2, 1) if current else b"")


def interactive(element_id, type_id=33, skill=39, current=True, enabled=True):
    return (num(6, element_id) + num(4, type_id) + (num(2, 1) if current else b"")
            + blob(5 if enabled else 3, num(1, 378) + num(3, skill)))


def map_message(map_id, entries):
    return num(10, map_id) + b"".join(blob(2, state) + blob(7, action) for state, action in entries)


def test_observed_chestnuts_count_maturity_independently_of_skill_availability():
    snapshots = []
    tracker = HarvestTracker(snapshots.append)
    tracker.handle("server", "jnj", map_message(100, [
        (stated(466503, 287), interactive(466503)),
        (stated(466502, 179, 1), interactive(466502, enabled=False)),
        (stated(466504, 473), interactive(466504, enabled=False)),
    ]))
    snapshot = snapshots[-1]
    assert snapshot.map_id == 100 and snapshot.changed_map
    assert snapshot.mature_count == 2
    assert snapshot.harvestable_count == 1
    assert [(r.element_id, r.harvestable) for r in snapshot.resources] == [
        (466502, False), (466503, True), (466504, False),
    ]
    assert [(r.element_id, r.name, r.status) for r in snapshot.resources] == [
        (466502, "Châtaignier", "Non mature"),
        (466503, "Châtaignier", "Mature"),
        (466504, "Châtaignier", "Mature"),
    ]
    # Delta omits the membership flag: keep the current-map membership.
    tracker.handle("server", "itw", blob(1, stated(466503, 287, 2, current=False)))
    assert snapshots[-1].mature_count == 1
    assert snapshots[-1].harvestable_count == 0
    assert not snapshots[-1].changed_map
    assert next(r for r in snapshots[-1].resources if r.element_id == 466503).status == "Récolte en cours"
    tracker.handle("server", "itw", blob(1, stated(466503, 287, 1, current=False)))
    assert [(r.element_id, r.state) for r in snapshots[-1].resources] == [(466502, 1), (466503, 1), (466504, 0)]


def test_neighbours_and_non_harvest_interactions_do_not_trigger_resource_alert():
    snapshots = []
    tracker = HarvestTracker(snapshots.append)
    tracker.handle("server", "jnj", map_message(100, [
        (stated(1, 100, current=False), interactive(1, current=False)),
        (stated(2, 101), interactive(2, type_id=99, skill=999999)),
    ]))
    assert snapshots[-1].resources == ()
    assert snapshots[-1].mature_count == 0
    assert snapshots[-1].other_elements == 1
    previous = snapshots[-1]
    tracker.handle("server", "itw", blob(1, stated(1, 100, 1)))
    assert snapshots[-1] is previous


def test_same_map_refresh_does_not_alert_but_returning_to_map_does():
    snapshots = []
    tracker = HarvestTracker(snapshots.append)
    populated = map_message(100, [(stated(1, 100), interactive(1))])
    tracker.handle("server", "jnj", populated)
    tracker.handle("server", "jnj", populated)
    tracker.handle("server", "jnj", map_message(101, []))
    assert snapshots[-1].resources == ()
    tracker.handle("server", "jnj", populated)
    assert [s.changed_map and s.mature_count > 0 for s in snapshots] == [True, False, False, True]


def test_unknown_states_stay_unknown_and_deltas_before_sync_are_ignored():
    snapshots = []
    tracker = HarvestTracker(snapshots.append)
    tracker.handle("server", "itw", blob(1, stated(1, 100)))
    tracker.handle("client", "jnj", map_message(100, []))
    tracker.handle("server", "chat", b"not decoded")
    assert snapshots == []
    tracker.handle("server", "jnj", map_message(100, [(stated(1, 100, 9), interactive(1))]))
    assert snapshots[-1].mature_count == 0
    assert snapshots[-1].resources[0].status == "État inconnu (9)"
    # An interactive update can remove the only recognised harvest action.
    tracker.handle("server", "itx", blob(1, interactive(1, skill=999999, current=False)))
    assert snapshots[-1].resources == ()
    assert not snapshots[-1].changed_map


def test_personal_availability_updates_without_changing_maturity():
    snapshots = []
    tracker = HarvestTracker(snapshots.append)
    # A usable non-harvest action must not make a disabled harvest available.
    mixed = interactive(1, enabled=False) + blob(5, num(3, 999999))
    tracker.handle("server", "jnj", map_message(100, [(stated(1, 100), mixed)]))
    assert snapshots[-1].mature_count == 1
    assert snapshots[-1].harvestable_count == 0
    tracker.handle("server", "itx", blob(1, interactive(1, current=False)))
    assert snapshots[-1].harvestable_count == 1
    assert not snapshots[-1].changed_map
    tracker.handle("server", "itx", blob(1, interactive(1, current=False, enabled=False)))
    assert snapshots[-1].mature_count == 1
    assert snapshots[-1].harvestable_count == 0
    # Another character/session snapshot must replace previous permissions.
    tracker.handle("server", "jnj", map_message(101, [(stated(1, 100), interactive(1))]))
    assert snapshots[-1].harvestable_count == 1
    tracker.handle("server", "jnj", map_message(102, [(stated(1, 100, 9), interactive(1))]))
    assert snapshots[-1].harvestable_count == 0
