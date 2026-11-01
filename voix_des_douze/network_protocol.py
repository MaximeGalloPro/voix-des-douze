"""Lecture seule du protocole observé sur Dofus 3.7.12.12 ; aucun accès réseau.

Les clés obfusquées sont spécifiques à cette version. Les paquets étrangers et
les chaînes privées ne sont ni publiés ni conservés par ce module.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import struct
from typing import Callable

from .vision import Dialogue

VERSION = "3.7.12.12"
MAX_FRAME = 1_000_000


class ProtocolError(ValueError):
    pass


def data_directory() -> Path:
    return Path.home() / "Library/Caches/VoixDesDouze/network" / VERSION


@dataclass(frozen=True)
class Catalog:
    names: dict[str, str]
    texts: dict[str, dict]

    @classmethod
    def load(cls, directory: Path | None = None):
        path = (directory or data_directory()) / "catalog.json"
        if not path.exists():
            raise ProtocolError("Préparez d’abord les données réseau avec le bouton dédié.")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != VERSION or not isinstance(raw.get("names"), dict) or not isinstance(raw.get("texts"), dict):
            raise ProtocolError("Catalogue réseau incompatible ; préparez de nouveau les données.")
        return cls(raw["names"], raw["texts"])


def varint(data, position=0):
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ProtocolError("Entier réseau incomplet.")
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ProtocolError("Entier réseau trop grand.")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
    raise ProtocolError("Entier réseau trop grand.")


def fields(data):
    result = []
    position = 0
    while position < len(data):
        tag, position = varint(data, position)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < 1 << 29:
            raise ProtocolError("Champ Protobuf invalide.")
        if wire == 0:
            value, position = varint(data, position)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, position = varint(data, position)
            else:
                length = 8 if wire == 1 else 4
            value = data[position:position + length]
            if len(value) != length:
                raise ProtocolError("Champ Protobuf incomplet.")
            position += length
        else:
            raise ProtocolError("Type de champ Protobuf inconnu.")
        result.append((number, wire, value))
    return result


def field(values, number, default=None):
    return next((value for n, wire, value in values if n == number), default)


def integer(values, number, default=None):
    value = field(values, number, default)
    if value is not None and not isinstance(value, int):
        raise ProtocolError("Identifiant réseau de type inattendu.")
    return value


def signed(value):
    return value - (1 << 64) if value >= 1 << 63 else value


def unwrap(data, depth=0):
    if depth > 4:
        return None
    try:
        values = fields(data)
    except ProtocolError:
        return None
    identity = field(values, 1)
    if isinstance(identity, bytes) and re.fullmatch(rb"type\.ankama\.com/[a-z]+", identity):
        body = field(values, 2, b"")
        return (identity.decode().rsplit("/", 1)[-1], body) if isinstance(body, bytes) else None
    for number, wire, value in values:
        if wire == 2:
            found = unwrap(value, depth + 1)
            if found:
                return found
    return None


class Conversation:
    """État par flux : carte -> identité -> ouverture -> répliques -> réponse/fin."""
    def __init__(self, catalog: Catalog, emit: Callable[[str, object], None]):
        self.catalog = catalog
        self.emit = emit
        self.map_id = None
        self.entities = {}
        self.npc = ""
        self.last = None
        self.active = False

    def invalidate(self):
        self.last = None
        self.emit("invalidate", None)

    def closed(self):
        self.active = False
        self.npc = ""
        self.invalidate()
        self.emit("closed", None)

    def handle(self, direction, key, raw):
        # All other messages, including chat, are discarded without decoding.
        if (direction, key) not in {("server", "jnj"), ("server", "ilx"), ("server", "ilf"),
                                     ("server", "kis"), ("client", "iky")}:
            return
        body = fields(raw)
        if direction == "server" and key == "jnj":
            self.closed()
            self.map_id = integer(body, 10)
            self.entities.clear()
            for number, wire, value in body:
                if number != 3 or wire != 2:
                    continue
                actor = fields(value)
                entity = integer(actor, 1)
                info = fields(field(actor, 3, b""))
                role = fields(field(info, 1, b""))
                npc = fields(field(role, 4, b""))
                template = integer(npc, 7)
                if entity is not None and template is not None:
                    self.entities[signed(entity)] = template
            self.emit("synchronized", None)
        elif direction == "server" and key == "ilx":
            self.closed()
            entity = integer(body, 5)
            template = self.entities.get(signed(entity)) if entity is not None and integer(body, 3) == self.map_id else None
            self.npc = self.catalog.names.get(str(template), "")
            if not self.npc:
                self.emit("unsupported", "PNJ non synchronisé : changez de carte puis revenez avant d’ouvrir le dialogue.")
                return
            self.active = True
            self.emit("opened", self.npc)
        elif direction == "server" and key == "ilf" and self.active:
            message_id = integer(body, 3)
            entry = self.catalog.texts.get(str(message_id))
            if not entry:
                self.invalidate()
                self.emit("unsupported", "Réplique absente du catalogue de cette version.")
                return
            parameters = [value for number, wire, value in body if number == 2 and wire == 2]
            if parameters or entry["params"]:
                self.invalidate()
                self.emit("unsupported", "Dialogue à paramètres non pris en charge par ce mode expérimental.")
                return
            text = html.unescape(re.sub(r"<[^>]+>", "", entry["text"])).strip()
            if not text:
                self.invalidate()
                return
            identity = (message_id, text)
            if identity != self.last:
                self.invalidate()
                self.last = identity
                self.emit("dialogue", Dialogue(self.npc, text))
        elif direction == "client" and key == "iky":
            self.invalidate()
            self.emit("replied", None)
        elif direction == "server" and key == "kis":
            self.closed()


class TCPStream:
    def __init__(self, direction, callback):
        self.direction, self.callback = direction, callback
        self.next_seq = None
        self.pending = {}
        self.buffer = bytearray()
        self.locked = False

    def feed(self, sequence, data):
        if not data:
            return
        if self.next_seq is None:
            self.next_seq = sequence
        # Normalize 32-bit TCP sequence numbers around the current stream cursor.
        sequence = self.next_seq + ((sequence - self.next_seq + (1 << 31)) % (1 << 32) - (1 << 31))
        if sequence + len(data) <= self.next_seq:
            return
        if sequence < self.next_seq:
            data = data[self.next_seq - sequence:]
            sequence = self.next_seq
        previous = self.pending.get(sequence, b"")
        if len(data) > len(previous):
            self.pending[sequence] = data
        while self.pending:
            start = min(self.pending)
            if start > self.next_seq:
                break
            chunk = self.pending.pop(start)
            skip = self.next_seq - start
            if skip < len(chunk):
                self.buffer.extend(chunk[skip:])
                self.next_seq += len(chunk) - skip
        if len(self.pending) > 256 or sum(map(len, self.pending.values())) + len(self.buffer) > 2 * MAX_FRAME:
            raise ProtocolError("Flux incomplet ou trop volumineux ; arrêtez puis relancez l’écoute.")
        if not self.locked:
            # Capture may start in the middle of a frame: lock only on a complete
            # valid Any envelope, never reinterpret a partial body as a dialogue.
            for start in range(len(self.buffer)):
                try:
                    length, begin = varint(self.buffer, start)
                except ProtocolError:
                    continue
                end = begin + length
                if not 0 < length <= MAX_FRAME or end > len(self.buffer):
                    continue
                if unwrap(bytes(self.buffer[begin:end])):
                    del self.buffer[:start]
                    self.locked = True
                    break
        while self.locked and self.buffer:
            try:
                length, begin = varint(self.buffer)
            except ProtocolError:
                if len(self.buffer) < 10:
                    return
                raise
            if not 0 < length <= MAX_FRAME:
                raise ProtocolError("Taille de message inattendue ; protocole incompatible.")
            end = begin + length
            if end > len(self.buffer):
                return
            found = unwrap(bytes(self.buffer[begin:end]))
            if not found:
                raise ProtocolError("Message incompatible avec le protocole vérifié.")
            del self.buffer[:end]
            self.callback(self.direction, *found)


class PacketReader:
    """PCAP Ethernet/IPv4 incrémental ; rien n’est écrit sur disque."""
    def __init__(self, callback, *, local_ip=None, local_port=None, remote_ip=None, remote_port=5555):
        self.buffer = bytearray()
        self.endian = None
        self.flows = {name: TCPStream(name, callback) for name in ("server", "client")}
        self.endpoints = (local_ip, local_port, remote_ip, remote_port)

    def feed(self, data):
        self.buffer.extend(data)
        if self.endian is None:
            if len(self.buffer) < 24:
                return
            magic = bytes(self.buffer[:4])
            if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
                self.endian = "<"
            elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
                self.endian = ">"
            else:
                raise ProtocolError("Format de capture inattendu.")
            if struct.unpack_from(self.endian + "I", self.buffer, 20)[0] != 1:
                raise ProtocolError("Cette version nécessite une interface Ethernet/Wi-Fi IPv4.")
            del self.buffer[:24]
        while len(self.buffer) >= 16:
            size = struct.unpack_from(self.endian + "I", self.buffer, 8)[0]
            if size > MAX_FRAME:
                raise ProtocolError("Paquet de taille inattendue.")
            if len(self.buffer) < size + 16:
                return
            packet = bytes(self.buffer[16:size + 16])
            del self.buffer[:size + 16]
            if len(packet) < 34 or packet[12:14] != b"\x08\x00":
                continue
            ip = packet[14:]
            if ip[9] != 6:
                continue
            header = (ip[0] & 15) * 4
            total = struct.unpack_from("!H", ip, 2)[0]
            if header < 20 or total > len(ip) or struct.unpack_from("!H", ip, 6)[0] & 0x3fff:
                raise ProtocolError("Paquet IPv4 tronqué ou fragmenté ; écoute interrompue.")
            tcp = ip[header:total]
            if len(tcp) < 20:
                raise ProtocolError("En-tête TCP incomplet.")
            source, destination, sequence = struct.unpack_from("!HHI", tcp)
            local_ip, local_port, remote_ip, remote_port = self.endpoints
            source_ip = ".".join(map(str, ip[12:16]))
            destination_ip = ".".join(map(str, ip[16:20]))
            direction = "server" if source == remote_port else "client"
            if local_ip is not None:
                endpoints = (source_ip, source, destination_ip, destination)
                expected = (remote_ip, remote_port, local_ip, local_port) if direction == "server" else (local_ip, local_port, remote_ip, remote_port)
                if endpoints != expected:
                    continue
            tcp_header = (tcp[12] >> 4) * 4
            if not 20 <= tcp_header <= len(tcp):
                raise ProtocolError("En-tête TCP invalide.")
            self.flows[direction].feed(sequence, tcp[tcp_header:])
            if tcp[13] & 5:
                raise ProtocolError("La connexion Dofus est fermée ; relancez l’écoute après reconnexion.")
