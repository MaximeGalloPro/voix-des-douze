"""Réplique résolue depuis les événements et les données locales du jeu."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Dialogue:
    npc: str
    text: str
