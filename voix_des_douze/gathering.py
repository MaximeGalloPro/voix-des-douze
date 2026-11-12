"""Local observed-map graph and uncertain resource respawn estimates."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
import statistics
import time

from .network_protocol import VERSION, fields, integer


class MapTransitions:
    """Only learn requested transitions confirmed by the server's next full map."""
    def __init__(self, emit, arrival=lambda event: None, clock=time.monotonic):
        self.emit = emit
        self.arrival = arrival
        self.clock = clock
        self.requested_at = None
        self.current = None
        self.requested = None

    def handle(self, direction, key, raw):
        if direction == "client" and key == "joc":
            self.requested = integer(fields(raw), 2)
            self.requested_at = self.clock()
        elif direction == "server" and key == "jnj":
            target = integer(fields(raw), 10)
            if self.current is not None and target != self.current and target == self.requested:
                self.emit((self.current, target))
            self.arrival({"origin": self.current, "target": target,
                          "requested": self.requested, "requested_at": self.requested_at,
                          "arrived_at": self.clock()})
            self.current = target
            self.requested = None
            self.requested_at = None


@dataclass(frozen=True)
class Destination:
    map_id: int
    element_id: int
    name: str
    x: int
    y: int
    world_id: int
    steps: int
    direction: str
    wait: float
    certainty: str
    source: str
    resource_count: int
    travel_seconds: float | None

    @property
    def command(self):
        # /travel coordinates designate a geographic position, not a particular interior.
        return f"/travel {self.x},{self.y}" if self.world_id == 1 else None


class GatheringMemory:
    def __init__(self, path, catalog=None, clock=time.time):
        self.clock = clock
        if catalog is None:
            catalog = json.loads((Path(__file__).resolve().parents[1] / "models/compass_catalog.json").read_text(encoding="utf-8"))
        if catalog["game_version"] != VERSION:
            raise ValueError("Catalogue de boussole incompatible avec la version réseau.")
        self.catalog = catalog
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS resources (
                profile TEXT, map_id INTEGER, element_id INTEGER, type_id INTEGER,
                name TEXT, state INTEGER, enabled INTEGER, seen REAL, cut REAL,
                PRIMARY KEY(profile,map_id,element_id));
            CREATE TABLE IF NOT EXISTS edges (
                profile TEXT, origin INTEGER, target INTEGER,
                PRIMARY KEY(profile,origin,target));
            CREATE TABLE IF NOT EXISTS samples (
                profile TEXT, type_id INTEGER, seconds REAL, observed REAL);
            CREATE INDEX IF NOT EXISTS samples_lookup ON samples(profile,type_id,observed DESC);
            CREATE TABLE IF NOT EXISTS preferences (
                profile TEXT, type_id INTEGER, enabled INTEGER NOT NULL, priority INTEGER NOT NULL,
                PRIMARY KEY(profile,type_id));
            CREATE TABLE IF NOT EXISTS harvests (
                profile TEXT, map_id INTEGER, element_id INTEGER, type_id INTEGER, observed REAL);
            CREATE TABLE IF NOT EXISTS travel (
                profile TEXT PRIMARY KEY, seconds REAL NOT NULL);
        """)
        self.profile = ""
        self.current = None
        self.previous = None
        self.cuts = {}
        self.revision = 0
        self._destinations = ()
        self._built_revision = -1
        self.seconds_per_map = None

    def profiles(self):
        rows = self.db.execute("""SELECT profile FROM resources UNION SELECT profile FROM edges
            UNION SELECT profile FROM samples UNION SELECT profile FROM preferences
            UNION SELECT profile FROM harvests UNION SELECT profile FROM travel ORDER BY profile""")
        return [row[0] for row in rows if row[0]]

    def select_profile(self, profile):
        self.profile = profile.strip()
        self.reset_session()
        row = self.db.execute("SELECT seconds FROM travel WHERE profile=?", (self.profile,)).fetchone()
        self.seconds_per_map = row[0] if row else None

    def save_travel_seconds(self, seconds):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO travel VALUES (?,?)", (self.profile, seconds))
        self.seconds_per_map = seconds

    def reset_session(self):
        self.current = None
        self.previous = None
        self.cuts.clear()
        self.revision += 1

    def observe_transition(self, origin, target):
        a, b = (self.catalog["maps"].get(str(m)) for m in (origin, target))
        # Coordinate neighbours only; teleports and unknown worlds aren't walk edges.
        if (not self.profile or not a or not b or a["world_id"] < 0
                or a["world_id"] != b["world_id"]
                or abs(a["x"]-b["x"]) + abs(a["y"]-b["y"]) != 1):
            return
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO edges VALUES (?,?,?)", (self.profile, origin, target))
        self.revision += 1

    def observe(self, snapshot):
        if not self.profile:
            return
        now = self.clock()
        if self.current != snapshot.map_id:
            self.previous = None
            self.cuts.clear()
        previous = {r.element_id: r for r in self.previous.resources} if self.previous else {}
        estimates = {}
        with self.db:
            for r in snapshot.resources:
                old = previous.get(r.element_id)
                record = self.db.execute("SELECT cut,enabled FROM resources WHERE profile=? AND map_id=? AND element_id=?",
                                         (self.profile, snapshot.map_id, r.element_id)).fetchone()
                eligible = r.harvest_enabled if r.state == 0 else (r.harvest_enabled or bool(record and record["enabled"]))
                cut = record["cut"] if record else None
                if r.state == 1 and old is not None and old.state in (0, 2):
                    cut = now
                    self.cuts[r.element_id] = now
                    self.db.execute("INSERT INTO harvests VALUES (?,?,?,?,?)",
                                    (self.profile, snapshot.map_id, r.element_id, r.type_id, now))
                elif r.state == 0:
                    started = self.cuts.pop(r.element_id, None)
                    if old is not None and old.state == 1 and started is not None and now > started:
                        self.db.execute("INSERT INTO samples VALUES (?,?,?,?)", (self.profile, r.type_id, now-started, now))
                    cut = None
                if r.state == 1 and cut is not None:
                    if r.type_id not in estimates:
                        estimates[r.type_id] = self.estimate(r.type_id)[0]
                    seconds = estimates[r.type_id]
                    # A fresh non-mature observation supersedes an expired forecast,
                    # but says nothing about who harvested or the type's true delay.
                    if seconds is not None and cut + seconds <= now:
                        cut = None
                self.db.execute("INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?,?,?)",
                                (self.profile, snapshot.map_id, r.element_id, r.type_id, r.name, r.state,
                                 int(eligible), now, cut))
            present = {r.element_id for r in snapshot.resources}
            for row in self.db.execute("SELECT element_id FROM resources WHERE profile=? AND map_id=?",
                                       (self.profile, snapshot.map_id)).fetchall():
                if row[0] not in present:
                    self.db.execute("DELETE FROM resources WHERE profile=? AND map_id=? AND element_id=?",
                                    (self.profile, snapshot.map_id, row[0]))
                    self.cuts.pop(row[0], None)
        self.current = snapshot.map_id
        self.previous = snapshot
        self.revision += 1

    def estimate(self, type_id):
        rows = self.db.execute("SELECT seconds FROM samples WHERE profile=? AND type_id=? ORDER BY observed DESC LIMIT 20",
                               (self.profile, type_id)).fetchall()
        if rows:
            values = [r[0] for r in rows]
            return statistics.median(values), f"Médiane de {len(values)} repousses observées sans quitter la carte"
        item = self.catalog["respawns"].get(str(type_id))
        return (item["seconds"], item["label"]) if item else (None, "Délai inconnu · observer une repousse sur place")

    def set_preference(self, type_id, enabled, priority):
        if not 1 <= priority <= 999:
            raise ValueError("La priorité doit être comprise entre 1 et 999.")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO preferences VALUES (?,?,?,?)",
                            (self.profile, type_id, int(enabled), priority))
        self.revision += 1

    def types(self):
        return self.db.execute("""SELECT r.type_id, MIN(r.name) AS name,
            COALESCE(p.enabled,1) AS enabled, COALESCE(p.priority,10) AS priority
            FROM resources r LEFT JOIN preferences p ON p.profile=r.profile AND p.type_id=r.type_id
            WHERE r.profile=? GROUP BY r.type_id ORDER BY name""", (self.profile,)).fetchall()

    def _paths(self):
        adjacency = {}
        for row in self.db.execute("SELECT origin,target FROM edges WHERE profile=?", (self.profile,)):
            adjacency.setdefault(row[0], []).append(row[1])
        paths = {self.current: (0, self.current)}
        queue = deque([self.current])
        while queue:
            origin = queue.popleft()
            for target in adjacency.get(origin, ()):
                if target not in paths:
                    paths[target] = (paths[origin][0]+1, target if origin == self.current else paths[origin][1])
                    queue.append(target)
        return paths

    def _build_candidates(self):
        paths = self._paths()
        rows = self.db.execute("""SELECT r.*, COALESCE(p.priority,10) AS priority FROM resources r
            LEFT JOIN preferences p ON p.profile=r.profile AND p.type_id=r.type_id
            WHERE r.profile=? AND r.enabled=1 AND COALESCE(p.enabled,1)=1""", (self.profile,)).fetchall()
        result = []
        estimates = {}
        for r in rows:
            position = self.catalog["maps"].get(str(r["map_id"]))
            if r["map_id"] == self.current or not position or r["map_id"] not in paths:
                continue
            if r["state"] == 0:
                due, source = 0, "Disponible à la dernière observation"
            elif r["state"] == 1 and r["cut"] is not None:
                if r["type_id"] not in estimates:
                    estimates[r["type_id"]] = self.estimate(r["type_id"])
                seconds, source = estimates[r["type_id"]]
                if seconds is None:
                    continue
                due = r["cut"] + seconds
            else:
                continue
            distance, first = paths[r["map_id"]]
            here = self.catalog["maps"].get(str(self.current))
            hop = self.catalog["maps"].get(str(first))
            if here and hop:
                dx, dy = hop["x"]-here["x"], hop["y"]-here["y"]
                direction = "Est →" if dx > 0 else "Ouest ←" if dx < 0 else "Sud ↓" if dy > 0 else "Nord ↑"
            else:
                direction = "Direction inconnue"
            result.append((dict(r), position, distance, direction, due, source))
        self._destinations = result
        self._built_revision = self.revision

    def recommend(self):
        if not self.profile or self.current is None:
            return None
        if self._built_revision != self.revision:
            self._build_candidates()
        now = self.clock()
        counts = {}
        for resource, _, _, _, due, _ in self._destinations:
            if due <= now:
                key = (resource["map_id"], resource["priority"])
                counts[key] = counts.get(key, 0) + 1
        def order(item):
            r, _, steps, _, due, _ = item
            if due <= now:
                count = counts[(r["map_id"], r["priority"])]
                cost = steps * (self.seconds_per_map if self.seconds_per_map is not None else 1) / count
                return (0, r["priority"], cost, steps, r["map_id"], r["element_id"])
            return (1, due, r["priority"], steps, r["map_id"], r["element_id"])
        if not self._destinations:
            return None
        r, p, steps, direction, due, source = min(self._destinations, key=order)
        certainty = "Vue disponible · à vérifier" if r["state"] == 0 else "Repousse estimée · non confirmée"
        return Destination(r["map_id"], r["element_id"], r["name"], p["x"], p["y"], p["world_id"],
                           steps, direction, max(0, due-now), certainty, source,
                           counts.get((r["map_id"], r["priority"]), 0),
                           steps*self.seconds_per_map if self.seconds_per_map is not None else None)

    def close(self):
        self.db.close()


def countdown(seconds):
    seconds = math.ceil(max(0, seconds))
    return f"{seconds//60} min {seconds%60:02d} s" if seconds else "Maintenant (à vérifier si estimé)"
