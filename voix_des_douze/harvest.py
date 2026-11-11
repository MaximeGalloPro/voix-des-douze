"""Passive resource state and server-advertised harvest availability."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from .network_protocol import VERSION, ProtocolError, fields, integer


@dataclass(frozen=True)
class Resource:
    element_id: int
    type_id: int
    name: str
    cell: int
    state: int
    harvest_enabled: bool

    @property
    def harvestable(self):
        return self.state == 0 and self.harvest_enabled

    @property
    def availability(self):
        if self.state != 0:
            return "État inconnu" if self.state not in (1, 2) else "Non · " + self.status.lower()
        return "Oui" if self.harvest_enabled else "Non · action indisponible"

    @property
    def status(self):
        return {0: "Mature", 1: "Non mature", 2: "Récolte en cours"}.get(self.state, f"État inconnu ({self.state})")


@dataclass(frozen=True)
class ResourceSnapshot:
    map_id: int
    changed_map: bool
    resources: tuple[Resource, ...]
    other_elements: int
    source: str

    @property
    def mature_count(self):
        return sum(resource.state == 0 for resource in self.resources)

    @property
    def harvestable_count(self):
        return sum(resource.harvestable for resource in self.resources)


class HarvestTracker:
    def __init__(self, emit: Callable[[ResourceSnapshot], None]):
        raw = json.loads((Path(__file__).resolve().parents[1] / "models/harvest.json").read_text(encoding="utf-8"))
        if raw["version"] != VERSION:
            raise ProtocolError("Métadonnées de récolte incompatibles avec le décodeur.")
        self.names = raw["interactive_names"]
        self.harvest_skills = frozenset(raw["harvest_skills"])
        self.emit = emit
        self.map_id = None
        self.states = {}
        self.interactives = {}

    @staticmethod
    def _state(body):
        # Observed 3.7.13.17: element id=3, cell=4, state=5 (proto3 zero omitted).
        element_id, cell = integer(body, 3), integer(body, 4)
        if element_id is None or cell is None:
            raise ProtocolError("Élément de carte sans identifiant ou case.")
        return element_id, (cell, integer(body, 5, 0))

    @staticmethod
    def _interactive(body):
        # Keep enabled (5) and disabled (3) actions separate for this character.
        element_id = integer(body, 6)
        if element_id is None:
            raise ProtocolError("Élément interactif sans identifiant.")
        enabled = frozenset(integer(fields(value), 3) for number, wire, value in body
                            if number == 5 and wire == 2)
        disabled = frozenset(integer(fields(value), 3) for number, wire, value in body
                             if number == 3 and wire == 2)
        return element_id, (integer(body, 4, -1), enabled, disabled)

    def handle(self, direction, key, raw):
        if direction != "server" or key not in ("jnj", "itw", "itx"):
            return
        body = fields(raw)
        changed_map = False
        if key == "jnj":
            map_id = integer(body, 10)
            if map_id is None:
                raise ProtocolError("Carte sans identifiant ; détection des ressources impossible.")
            states, interactives = {}, {}
            for number, wire, value in body:
                if wire != 2 or number not in (2, 7):
                    continue
                nested = fields(value)
                # Full snapshots also include neighbouring-map elements.
                if integer(nested, 2, 0) != 1:
                    continue
                element_id, entry = self._state(nested) if number == 2 else self._interactive(nested)
                (states if number == 2 else interactives)[element_id] = entry
            changed_map = self.map_id != map_id
            self.map_id, self.states, self.interactives = map_id, states, interactives
        else:
            if self.map_id is None:
                return  # A delta cannot establish current-map membership.
            updates = [fields(value) for number, wire, value in body if number == 1 and wire == 2]
            applied = False
            for nested in updates:
                element_id, entry = self._state(nested) if key == "itw" else self._interactive(nested)
                target = self.states if key == "itw" else self.interactives
                # Delta flags are not used to infer membership; retain the snapshot boundary.
                if element_id in target:
                    target[element_id] = entry
                    applied = True
            if not applied:
                return
        resources = []
        for element_id, (type_id, enabled, disabled) in self.interactives.items():
            if element_id not in self.states or not (enabled | disabled).intersection(self.harvest_skills):
                continue
            cell, state = self.states[element_id]
            resources.append(Resource(element_id, type_id, self.names.get(str(type_id), f"Type {type_id}"),
                                      cell, state, bool(enabled.intersection(self.harvest_skills))))
        self.emit(ResourceSnapshot(self.map_id, changed_map,
                                   tuple(sorted(resources, key=lambda resource: (resource.name, resource.element_id))),
                                   len(self.interactives) - len(resources), key))
