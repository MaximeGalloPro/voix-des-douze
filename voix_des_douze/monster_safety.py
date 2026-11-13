"""Fail-closed monster distance and conservative player-sprite click guards."""
from threading import RLock

from .network_protocol import ProtocolError, fields, field, integer, signed, varint


def cell_coordinates(cell):
    row, column = divmod(cell, 14)
    return column + (row + 1) // 2, column - row // 2


_COORDINATES = tuple(cell_coordinates(cell) for cell in range(560))
_CELLS = {point: cell for cell, point in enumerate(_COORDINATES)}


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
                self.groups[signed(entity)] = cell
                self.reserved.add(cell)
            if key == 'jnj':
                self.synchronized = True
        else:
            entity = integer(body, 4)
            if entity is None or (signed(entity) not in self.groups and signed(entity) not in self.players):
                return
            entity = signed(entity)
            path = unpack_ids(body, 1)
            cells = path_cells(path)
            if entity in self.players:
                if path[0] != self.players[entity]:
                    # A server update can stop or redirect a player before the
                    # previously announced destination. Keep the old swept area
                    # protected, but do not invalidate unrelated actors/clicks.
                    cells.update(self.player_paths[entity])
                self.players[entity] = path[-1]
                # A continuous following update or full actor state replaces it.
                self.player_paths[entity] = cells
                return
            if path[0] != self.groups[entity]:
                raise ProtocolError("Position de monstre discontinue ; changez de carte pour resynchroniser.")
            self.groups[entity] = path[-1]
            # No observed animation-completion signal: never expire a swept area
            # by guessing a timeout, nor discard a disappeared group's last area.
            self.reserved.update(cells)

    def send(self, map_id, coordinates, callback):
        """Read the worker's latest state at dispatch, not a queued Qt snapshot."""
        with self.lock:
            if not self.synchronized or self.map_id != map_id:
                raise RuntimeError("Protection anti-combat : positions non synchronisées ; changez de carte pendant l’écoute.")
            x, y = coordinates
            if any(abs(x - u) + abs(y - v) < 2 - 1e-9 for cell in self.reserved
                   for u, v in (_COORDINATES[cell],)):
                raise MonsterProximity("Protection anti-combat : clic annulé à moins de 2 cases d’un groupe ou de son trajet observé sur cette carte.")
            # Project a broad upright sprite envelope: one tile-width on each side,
            # two tile-widths above the feet and half a tile-width below.
            for cells in self.player_paths.values():
                for cell in cells:
                    u, v = _COORDINATES[cell]
                    dx = (x + y - u - v) / 2
                    dy = x - y - u + v
                    if abs(dx) <= 1 + 1e-9 and -8 - 1e-9 <= dy <= 2 + 1e-9:
                        raise PlayerOverlap("Protection personnage : cible trop proche du dessin d’un joueur ; clic non envoyé.")
            callback()
