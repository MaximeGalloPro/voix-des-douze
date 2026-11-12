"""Local prediction/arrival comparisons; server observations, never screen recognition."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile


class CompassJournal:
    MAX_BYTES = 512 * 1024

    def __init__(self, directory, error):
        self.directory = Path(directory)
        self.error = error
        self.path = None
        self.file = None
        self.failed = False
        self.profile = None
        self.pending = {}
        self.selected = None
        self.sequence = 0

    def reset(self, profile=None):
        self.profile = profile
        self.pending.clear()
        self.selected = None

    def sync_profile(self, profile):
        if profile != self.profile:
            self.reset(profile)

    def record(self, event, now, **fields):
        if self.failed:
            return False
        try:
            if self.file is None:
                self.directory.mkdir(parents=True, exist_ok=True)
                fd, path = tempfile.mkstemp(prefix="boussole-", suffix=".jsonl", dir=self.directory)
                self.path = Path(path)
                self.file = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
            line = json.dumps({"heure": now, "étape": event, "profil": self.profile,
                               **fields}, ensure_ascii=False) + "\n"
            if self.file.tell() + len(line.encode("utf-8")) > self.MAX_BYTES:
                raise OSError("limite de 512 Kio atteinte ; relancez l’application pour un nouveau journal")
            self.file.write(line)
            return True
        except OSError as exc:
            self.failed = True
            self.close()
            self.error(f"Journal de boussole impossible : {exc}")
            return False

    def prediction(self, memory, destination):
        self.sync_profile(memory.profile)
        if destination is None:
            self.selected = None
            return
        d = destination
        row = memory.db.execute("SELECT * FROM resources WHERE profile=? AND map_id=? AND element_id=?",
                                (memory.profile, d.map_id, d.element_id)).fetchone()
        if row is None:
            return
        observed = dict(row)
        observed.pop("profile")
        seconds, _ = memory.estimate(row["type_id"])
        due = row["cut"] + seconds if row["state"] == 1 and row["cut"] is not None and seconds is not None else None
        now = memory.clock()
        ready = row["state"] == 0 or (due is not None and due <= now)
        signature = (memory.current, tuple(observed.values()), seconds, d.source, ready,
                     d.resource_count, d.travel_seconds, d.steps)
        if signature == self.selected:
            return
        self.selected = signature
        self.sequence += 1
        prediction = {"id": self.sequence, "version_jeu": memory.catalog["game_version"],
                      "carte_origine": memory.current,
                      "destination": asdict(d), "dernière_observation": observed,
                      "durée_estimée_s": seconds, "échéance": due,
                      "prédite_prête": ready, "prédiction_à": now}
        if self.record("prédiction", now, **prediction):
            self.pending[(d.map_id, d.element_id)] = prediction

    def arrival(self, profile, snapshot, now):
        self.sync_profile(profile)
        if snapshot.source != "jnj":
            return
        resources = {r.element_id: r for r in snapshot.resources}
        for key in list(self.pending):
            map_id, element_id = key
            if map_id != snapshot.map_id:
                continue
            prediction = self.pending.pop(key)
            r = resources.get(element_id)
            due = prediction["échéance"]
            expected_ready = prediction["dernière_observation"]["state"] == 0 or (due is not None and due <= now)
            if r is None:
                result = "ressource absente"
            elif r.type_id != prediction["dernière_observation"]["type_id"]:
                result = "identité différente"
            elif r.state == 0:
                result = "prête" if r.harvest_enabled else "mature mais action indisponible"
            elif r.state == 1:
                result = "prédite prête mais non mature" if expected_ready else "arrivée avant échéance, non mature"
            elif r.state == 2:
                result = "récolte en cours"
            else:
                result = "état inconnu"
            self.record("comparaison à l’arrivée", now, prédiction=prediction["id"],
                        carte=map_id, élément=element_id, résultat=result,
                        attendue_prête_à_arrivée=expected_ready,
                        écart_échéance_s=now-due if due is not None else None,
                        délai_depuis_prédiction_s=now-prediction["prédiction_à"],
                        observation=asdict(r) if r else None,
                        source_observation=snapshot.source)

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
