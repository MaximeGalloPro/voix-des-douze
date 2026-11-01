"""Contrats du flux réseau : transitions, annulation, isolation et TCP fragmenté."""
import socket
import struct

import pytest

from voix_des_douze.network_protocol import Catalog, Conversation, PacketReader, ProtocolError, TCPStream


def vint(value):
    value %= 1 << 64
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result) + bytes([value])


def num(key, value):
    return vint(key << 3) + vint(value)


def blob(key, value):
    return vint((key << 3) | 2) + vint(len(value)) + value


def envelope(key, body):
    any_body = blob(1, b"type.ankama.com/" + key.encode()) + blob(2, body)
    frame = blob(2, blob(1, any_body))
    return vint(len(frame)) + frame


def map_body(map_id=100, template=59):
    npc = blob(4, num(7, template))
    actor = num(1, -20001) + blob(3, blob(1, npc))
    return blob(3, actor) + num(10, map_id)


def opened(map_id=100):
    return num(3, map_id) + num(5, -20001)


def make_conversation():
    events = []
    catalog = Catalog({"59": {"id": 59, "name": "Bûcheron"}, "60": {"id": 60, "name": "Banquière"}}, {
        "33": {"text": "Bonjour, voyageur.", "params": []},
        "34": {"text": "Votre compte contient 12 kamas.", "params": []},
        "35": {"text": "Bonjour #1.", "params": ["PN"]},
    })
    return Conversation(catalog, lambda kind, value: events.append((kind, value))), events


def test_replies_cancel_old_text_and_identical_reopening_is_read_again():
    state, events = make_conversation()
    state.handle("server", "jnj", map_body())
    state.handle("server", "ilx", opened())
    state.handle("server", "ilf", num(3, 33))
    state.handle("server", "ilf", num(3, 33))
    assert len([value for kind, value in events if kind == "dialogue"]) == 1
    state.handle("client", "iky", num(1, 88))
    assert events[-2:] == [("invalidate", None), ("replied", None)]
    state.handle("server", "ilf", num(3, 34))
    assert events[-1][1].text == "Votre compte contient 12 kamas."
    state.handle("server", "kis", num(1, 1))
    state.handle("server", "ilf", num(3, 33))
    assert events[-1] == ("closed", None)
    state.handle("server", "ilx", opened())
    state.handle("server", "ilf", num(3, 33))
    assert [value.text for kind, value in events if kind == "dialogue"] == [
        "Bonjour, voyageur.", "Votre compte contient 12 kamas.", "Bonjour, voyageur."]


def test_entity_ids_are_scoped_to_the_observed_map():
    state, events = make_conversation()
    state.handle("server", "ilx", opened())
    assert events[-1][0] == "unsupported"
    state.handle("server", "jnj", map_body())
    state.handle("server", "jnj", map_body(101, 60))
    state.handle("server", "ilx", opened())
    assert events[-1][0] == "unsupported"
    state.handle("server", "ilx", opened(101))
    state.handle("server", "ilf", num(3, 34))
    assert events[-1][1].npc == "Banquière"


def test_unknown_or_parameterized_text_cancels_instead_of_speaking_placeholder():
    state, events = make_conversation()
    state.handle("server", "jnj", map_body())
    state.handle("server", "ilx", opened())
    for body in (num(3, 999), num(3, 35), blob(2, b"PlayerName") + num(3, 33)):
        state.handle("server", "ilf", body)
        assert events[-2][0] == "invalidate"
        assert events[-1][0] == "unsupported"
    assert not any(kind == "dialogue" for kind, _ in events)
    state.handle("server", "chat_message", b"private chat is not even parsed")
    assert events[-1][0] == "unsupported"


def test_tcp_reassembly_handles_overlap_retransmit_reordering_and_sequence_wrap():
    received = []
    stream = TCPStream("server", lambda direction, key, body: received.append((key, body)))
    first = envelope("ilx", opened())
    second = envelope("ilf", num(3, 33))
    sequence = (1 << 32) - 12
    wire = first + second
    stream.feed(sequence, wire[:8])
    stream.feed((sequence + 20) % (1 << 32), wire[20:])
    stream.feed((sequence + 5) % (1 << 32), wire[5:25])
    stream.feed(sequence, wire[:8])
    assert received == [("ilx", opened()), ("ilf", num(3, 33))]


def packet(payload, sequence=10, local_port=40000):
    tcp = struct.pack("!HHIIBBHHH", 5555, local_port, sequence, 0, 5 << 4, 0x18, 4096, 0, 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, len(tcp) + 20, 0, 0, 64, 6, 0,
                     socket.inet_aton("192.0.2.1"), socket.inet_aton("127.0.0.1")) + tcp
    ethernet = b"\0" * 12 + b"\x08\x00" + ip
    return struct.pack("<IIII", 0, 0, len(ethernet), len(ethernet)) + ethernet


def pcap(*packets):
    return struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 524288, 1) + b"".join(packets)


def test_incremental_pcap_ignores_another_clients_connection():
    state, events = make_conversation()
    reader = PacketReader(state.handle, local_ip="127.0.0.1", local_port=40000, remote_ip="192.0.2.1")
    wire = envelope("jnj", map_body()) + envelope("ilx", opened()) + envelope("ilf", num(3, 33))
    private = envelope("jnj", map_body(999, 60)) + envelope("ilx", opened(999)) + envelope("ilf", num(3, 34))
    data = pcap(packet(private, local_port=40001), packet(wire[:30]), packet(wire[30:], sequence=40))
    for i in range(0, len(data), 7):
        reader.feed(data[i:i + 7])
    dialogues = [value for kind, value in events if kind == "dialogue"]
    assert [(d.npc, d.text) for d in dialogues] == [("Bûcheron", "Bonjour, voyageur.")]


def test_incompatible_frame_fails_instead_of_reusing_old_dialogue():
    stream = TCPStream("server", lambda *args: None)
    valid = envelope("ilx", opened())
    stream.feed(0, valid)
    with pytest.raises(ProtocolError):
        stream.feed(len(valid), b"\x01\x00")


def test_network_dialogue_to_persisted_voice_and_real_piper_offline(tmp_path, monkeypatch):
    from array import array
    from voix_des_douze.settings import Store, model_directory
    from voix_des_douze.speech import SpeechEngine, available_voices
    voices = available_voices(model_directory())
    if not voices:
        pytest.skip("Préparez les modèles avec Installer.command")

    def deny(*args, **kwargs):
        raise AssertionError("Le décodage et Piper ne doivent pas ouvrir de connexion")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    state, events = make_conversation()
    reader = PacketReader(state.handle)
    reader.feed(pcap(packet(envelope("jnj", map_body()) + envelope("ilx", opened()) + envelope("ilf", num(3, 33)))))
    dialogue = next(value for kind, value in events if kind == "dialogue")
    store = Store(tmp_path / "settings.json")
    choice = store.voice_for(dialogue.npc, [v.key for v in voices])
    segments = list(SpeechEngine(model_directory()).synthesize(dialogue.text, next(v for v in voices if v.key == choice)))
    signal = array("h", b"".join(segment.pcm for segment in segments))
    assert max(signal) - min(signal) > 1000
    assert 0.5 < len(signal) / segments[0].sample_rate < 15
    assert Store(store.path).voice_for(dialogue.npc, [v.key for v in voices]) == choice
