"""Fail-closed monster distance and conservative player-sprite click guards."""
from threading import RLock
import time

from .network_protocol import ProtocolError, fields, field, integer, signed, varint


def cell_coordinates(cell):
    row, column = divmod(cell, 14)
    return column + (row + 1) // 2, column - row // 2


_COORDINATES = tuple(cell_coordinates(cell) for cell in range(560))
_CELLS = {point: cell for cell, point in enumerate(_COORDINATES)}

MONSTER_HALF_WIDTH = 0.5
MONSTER_HALF_HEIGHT = 2.0
PLAYER_HALF_WIDTH = 0.5
PLAYER_ABOVE = 2.75
PLAYER_BELOW = 1.25
PASSAGE_STEP_SECONDS = 0.3
PASSAGE_FADE_SECONDS = 1.4


def unpack_ids(body, number):
    result = []
    for n, wire, value in body:
        if n != number:
            continue
        if wire == 0:
            result.append(signed(value))
        elif wire == 2:
            offset = 0
            while offset < len(value):
                item, offset = varint(value, offset)
                result.append(signed(item))
        else:
            raise ProtocolError("Liste de cases des monstres incompatible.")
    return result


def path_cells(path):
    if not path or any(not 0 <= cell < 560 for cell in path):
        raise ProtocolError("Déplacement de monstre sans cases valides.")
    result = set(path)
    for start, end in zip(path, path[1:]):
        x, y = _COORDINATES[start]
        u, v = _COORDINATES[end]
        dx, dy = u - x, v - y
        if dx and dy and abs(dx) != abs(dy):
            raise ProtocolError("Trajet de monstre non rectiligne ; protection non synchronisée.")
        steps = max(abs(dx), abs(dy))
        sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
        for step in range(1, steps):
            cell = _CELLS.get((x + sx * step, y + sy * step))
            if cell is None:
                raise ProtocolError("Trajet de monstre hors carte.")
            result.add(cell)
    return result


class MonsterProximity(RuntimeError):
    """The target intersects a monster's protected ground area."""


class PlayerOverlap(RuntimeError):
    """The target may hit a player sprite rather than the ground."""


def passage_expiry(path, now):
    expiry = {}
    elapsed = 0.0
    for start, end in zip(path, path[1:]):
        u, v = _COORDINATES[start]
        for cell in path_cells([start, end]):
            x, y = _COORDINATES[cell]
            expiry[cell] = now + elapsed + max(abs(x - u), abs(y - v)) * PASSAGE_STEP_SECONDS + PASSAGE_FADE_SECONDS
        x, y = _COORDINATES[end]
        elapsed += max(abs(x - u), abs(y - v)) * PASSAGE_STEP_SECONDS
    return expiry


class MonsterSafety:
    def __init__(self):
        self.lock = RLock()
        self.clear()

    def clear(self):
        with self.lock:
            self.map_id = None
            self.synchronized = False
            self.groups = {}
            self.reserved = set()
            self.players = {}
            self.player_paths = {}
            self.player_expiry = {}
            self.monster_paths = {}
            self.monster_expiry = {}

    def handle(self, direction, key, raw):
        if direction == 'client' and key == 'joc':
            self.clear()
            return
        if direction != 'server' or key not in {'jnj', 'jns', 'jnz'}:
            return
        with self.lock:
            try:
                self._handle(key, fields(raw))
            except (ProtocolError, ValueError):
                self.synchronized = False
                raise

    def _handle(self, key, body):
        if key == 'jnj':
            self.clear()
            self.map_id = integer(body, 10)
            if self.map_id is None:
                raise ProtocolError("Carte sans identifiant pour la protection anti-combat.")
        elif not self.synchronized:
            return
        if key in {'jnj', 'jns'}:
            for number, wire, raw in body:
                if wire != 2 or number not in ({3} if key == 'jnj' else {1, 2}):
                    continue
                actor = fields(raw)
                info = fields(field(actor, 3, b''))
                role = fields(field(info, 1, b''))
                entity = integer(actor, 1)
                if field(role, 3) is not None:
                    position = fields(field(actor, 2, b''))
                    cell = integer(position, 3)
                    if entity is None or cell is None or not 0 <= cell < 560:
                        raise ProtocolError("Joueur sans identifiant ou position valide.")
                    entity = signed(entity)
                    if entity in self.groups:
                        raise ProtocolError("Transformation de monstre non synchronisée.")
                    self.players[entity] = cell
                    self.player_paths[entity] = {cell}
                    self.player_expiry[entity] = {}
                    continue
                if entity is not None and signed(entity) in self.players:
                    raise ProtocolError("Transformation de joueur non synchronisée.")
                if field(role, 5) is None:
                    # A known monster becoming another actor type is unverified.
                    if entity is not None and signed(entity) in self.groups:
                        raise ProtocolError("Transformation de monstre non synchronisée.")
                    if not role:
                        raise ProtocolError("Acteur de carte non reconnu par la protection anti-combat.")
                    continue
                position = fields(field(actor, 2, b''))
                cell = integer(position, 3)
                if entity is None or cell is None or not 0 <= cell < 560:
                    raise ProtocolError("Groupe de monstres sans identifiant ou position valide.")
                entity = signed(entity)
                self.groups[entity] = cell
                self.monster_paths[entity] = {cell}
                self.monster_expiry[entity] = {}
                self.reserved = set().union(*self.monster_paths.values())
            if key == 'jnj':
                self.synchronized = True
        else:
            entity = integer(body, 4)
            if entity is None or (signed(entity) not in self.groups and signed(entity) not in self.players):
                return
            entity = signed(entity)
            path = unpack_ids(body, 1)
            path_cells(path)  # Validate before changing either actor tracker.
            now = time.monotonic()
            self.expire_paths(now)
            if entity in self.players:
                expiry = {}
                if path[0] != self.players[entity]:
                    expiry.update(self.player_expiry[entity])
                    expiry[self.players[entity]] = now + PASSAGE_FADE_SECONDS
                expiry.update(passage_expiry(path, now))
                expiry.pop(path[-1], None)
                self.players[entity] = path[-1]
                self.player_expiry[entity] = expiry
                self.player_paths[entity] = set(expiry) | {path[-1]}
                return
            if path[0] != self.groups[entity]:
                raise ProtocolError("Position de monstre discontinue ; changez de carte pour resynchroniser.")
            expiry = passage_expiry(path, now)
            expiry.pop(path[-1], None)
            self.groups[entity] = path[-1]
            self.monster_expiry[entity] = expiry
            self.monster_paths[entity] = set(expiry) | {path[-1]}
            self.reserved = set().union(*self.monster_paths.values())

    def expire_paths(self, now=None):
        """Expire estimated passages, never an actor's last announced position."""
        with self.lock:
            now = time.monotonic() if now is None else now
            for paths, deadlines in ((self.player_paths, self.player_expiry),
                                     (self.monster_paths, self.monster_expiry)):
                for entity, expiry in deadlines.items():
                    for cell in tuple(expiry):
                        if expiry[cell] <= now:
                            del expiry[cell]
                            paths[entity].discard(cell)
            self.reserved = set().union(*self.monster_paths.values())

    def passage_opacity(self, paths, deadlines):
        """Return the same active areas used by send, with a visual fade."""
        with self.lock:
            now = time.monotonic()
            self.expire_paths(now)
            return {entity: {cell: (max(0.0, min(1.0, (expiry[cell] - now) / PASSAGE_FADE_SECONDS))
                                   if cell in expiry else 1.0)
                             for cell in paths[entity]}
                    for entity, expiry in deadlines.items()}

    def player_opacity(self):
        return self.passage_opacity(self.player_paths, self.player_expiry)

    def monster_opacity(self):
        return self.passage_opacity(self.monster_paths, self.monster_expiry)

    def send(self, map_id, coordinates, callback):
        """Read the worker's latest state at dispatch, not a queued Qt snapshot."""
        with self.lock:
            if not self.synchronized or self.map_id != map_id:
                raise RuntimeError("Protection anti-combat : positions non synchronisées ; changez de carte pendant l’écoute.")
            self.expire_paths()
            x, y = coordinates
            if any(abs((x + y - u - v) / 2) < MONSTER_HALF_WIDTH - 1e-9
                   and abs(x - y - u + v) < MONSTER_HALF_HEIGHT - 1e-9
                   for cell in self.reserved for u, v in (_COORDINATES[cell],)):
                raise MonsterProximity("Protection anti-combat : clic annulé dans le carré protégé d’un groupe ou de son trajet observé sur cette carte.")
            # Square screen envelope, retaining the previous player's center.
            for cells in self.player_paths.values():
                for cell in cells:
                    u, v = _COORDINATES[cell]
                    dx = (x + y - u - v) / 2
                    dy = x - y - u + v
                    if (abs(dx) <= PLAYER_HALF_WIDTH + 1e-9
                            and -PLAYER_ABOVE - 1e-9 <= dy <= PLAYER_BELOW + 1e-9):
                        raise PlayerOverlap("Protection personnage : cible trop proche du dessin d’un joueur ; clic non envoyé.")
            callback()
