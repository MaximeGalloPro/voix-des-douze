from voix_des_douze.settings import Store


def test_voice_persistence_normalization_and_missing_name(tmp_path):
    path = tmp_path / "settings.json"
    store = Store(path)
    choices = ["siwis", "mls:0", "tom"]
    first = store.voice_for("Kerubim Crépin", choices)
    second = store.voice_for("Nacf Virgil", choices)
    assert first != second
    store.assign("  KERUBIM CRÉPIN ", "tom")
    reopened = Store(path)
    assert reopened.voice_for("Kerubim Cre\u0301pin", choices) == "tom"
    assert reopened.voice_for("Nacf Virgil", choices) == second
    reopened.settings.default_voice = "mls:0"
    assert reopened.voice_for("", choices) == "mls:0"
    assert "" not in reopened.settings.assignments
    assert reopened.voice_for("Kerubim Crépin", ["siwis"]) == "siwis"


def test_corrupt_configuration_is_preserved(tmp_path):
    import pytest
    path = tmp_path / "settings.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Configuration illisible"):
        Store(path)
    assert path.read_text() == "broken"


def test_settings_migration_preserves_voices_and_original_file(tmp_path):
    import json
    path = tmp_path / "settings.json"
    original = {
        "automatic": False, "volume": 0.6, "speed": 1.2, "default_voice": "tom",
        "assignments": {"agride shakoku": "siwis"},
        "regions": {"old-window": {"text": [0, 0, 1, 1]}},
        "read_responses": True, "response_voice": "mls:0",
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    store = Store(path)
    assert store.voice_for("Agride Shakoku", ["tom", "siwis"]) == "siwis"
    assert store.settings.automatic is False
    assert store.settings.volume == 0.6 and store.settings.speed == 1.2
    assert store.settings.default_voice == "tom"
    assert json.loads(path.with_name("settings-before-network.json").read_text()) == original
    assert not {"regions", "read_responses", "response_voice"}.intersection(json.loads(path.read_text()))
    Store(path)
    assert json.loads(path.with_name("settings-before-network.json").read_text()) == original


def test_engine_assignments_and_defaults_survive_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    store = Store(path)
    for engine, voices in (("piper", ["siwis", "tom"]), ("sopro", ["sopro:a", "sopro:b"]),
                           ("pocket", ["pocket:a", "pocket:b"])):
        store.assign("Kerubim Crépin", voices[1], engine)
        store.set_default(engine, voices[0])
        assert store.voice_for("Kerubim Crépin", voices, engine) == voices[1]
        assert store.voice_for("", voices, engine) == voices[0]
    store.settings.engine = "pocket"
    store.save()
    reopened = Store(path)
    assert reopened.settings.engine == "pocket"
    assert reopened.voice_for("KERUBIM CRÉPIN", ["siwis", "tom"]) == "tom"
    assert reopened.voice_for("Kerubim Crépin", ["sopro:a", "sopro:b"], "sopro") == "sopro:b"
    assert reopened.voice_for("Kerubim Crépin", ["pocket:a", "pocket:b"], "pocket") == "pocket:b"
    assert reopened.default_for("piper") == "siwis"
    assert reopened.default_for("sopro") == "sopro:a"
    assert reopened.default_for("pocket") == "pocket:a"
