"""Temporary bounded position diagnostics; no dialogue, names or raw packets."""
from uuid import uuid4

from .idle_log import IdleJournal
from .monster_safety import unpack_ids
from .network_protocol import ProtocolError, field, fields, integer, signed
from .settings import data_directory

# Temporary investigation switch. Does not alter protocol or click safety.
POSITION_DEBUG = True


def numeric_fields(body):
    return [{'champ': number, 'valeur': signed(value)}
            for number, wire, value in body if wire == 0]


class PositionDebug:
    def __init__(self, error):
        self.journal = IdleJournal(data_directory() / 'debug', error)
        self.journal.path = self.journal.path.with_name('positions-debug.jsonl')
        self.session = uuid4().hex
        self.write('session ouverte', {})

    def write(self, stage, details):
        self.journal.write(self.session, stage, {}, details)

    def packet(self, safety, direction, key, raw):
        if ((direction == 'server' and key not in {'jnj', 'jns', 'jnz', 'jnk', 'jnt', 'joo'})
                or (direction == 'client' and key not in {'jok', 'jny', 'jnt', 'joc', 'jpe', 'jov', 'jow'})):
            return
        details = {'sens': direction, 'message': key}
        try:
            body = fields(raw)
            details['champs_numériques'] = numeric_fields(body)
            with safety.lock:
                details['carte_avant'] = safety.map_id
                details['synchronisé_avant'] = safety.synchronized
                if key == 'jnz':
                    entity = integer(body, 4)
                    entity = signed(entity) if entity is not None else None
                    details['acteur'] = entity
                    details['type_mémorisé'] = ('joueur' if entity in safety.players else
                                                'monstre' if entity in safety.groups else 'inconnu')
                    details['case_attendue'] = safety.players.get(entity, safety.groups.get(entity))
            if key in {'jnz', 'jok', 'jnk'}:
                details['chemin_transmis'] = unpack_ids(body, 1)
            if direction == 'server' and key in {'jnj', 'jns'}:
                details['acteurs'] = []
                for number, wire, value in body:
                    if wire != 2 or number not in ({3} if key == 'jnj' else {1, 2}):
                        continue
                    actor = fields(value)
                    entity = integer(actor, 1)
                    position = fields(field(actor, 2, b''))
                    role = fields(field(fields(field(actor, 3, b'')), 1, b''))
                    details['acteurs'].append({
                        'liste': number,
                        'acteur': signed(entity) if entity is not None else None,
                        'type': ('joueur' if field(role, 3) is not None else
                                 'monstre' if field(role, 5) is not None else 'autre'),
                        'position_numérique': numeric_fields(position),
                        'position_sous_messages': [
                            {'champ': n, 'valeurs': numeric_fields(fields(v))}
                            for n, w, v in position if w == 2],
                    })
        except (ProtocolError, ValueError) as exc:
            # Diagnostics never replace the production parser's error handling.
            details['erreur_décodage_debug'] = str(exc)
        self.write('message position avant traitement', details)
