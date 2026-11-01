"""Migration sûre et conservation des identités dans le catalogue local."""
import json

import pytest

from scripts import prepare_network
from voix_des_douze.network_protocol import Catalog, VERSION


def sources(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": VERSION, "repository": "test/local", "release": "fixture", "files": []}))
    monkeypatch.setattr(prepare_network, "MANIFEST", manifest)
    (tmp_path / "i18n_fr.json").write_text(json.dumps({"10": "Même nom", "20": "Bonjour."}))
    records = [
        {"id": 1, "nameId": 10, "gender": 0, "look": "{714}", "dialogData": {"Array": [{"moodId": 2}]}},
        {"id": 2, "nameId": 10, "gender": 1, "look": "{5|||90}"},
        {"id": 3, "nameId": 999, "gender": 7, "look": "{999}", "actions": {"Array": []}},
    ]
    (tmp_path / "npcsdata.json").write_text(json.dumps({"references": {"RefIds": [
        {"type": {"class": "NpcData"}, "data": row} for row in records]}}))
    (tmp_path / "npcmessagesdata.json").write_text(json.dumps({"references": {"RefIds": [
        {"type": {"class": "NpcMessageData"}, "data": {
            "id": 9, "messageId": 20, "messageParams": {"Array": []}}}]}}))
    return {"version": VERSION, "source": "test/local@fixture", "names": {"1": "Même nom", "2": "Même nom"},
            "texts": {"9": {"text": "Bonjour.", "params": []}}}


def test_metadata_migration_keeps_distinct_ids_and_untranslated_npcs(tmp_path, monkeypatch):
    legacy = sources(tmp_path, monkeypatch)
    target = tmp_path / "catalog.json"
    original = json.dumps(legacy).encode()
    target.write_bytes(original)
    prepare_network.prepare(tmp_path)
    catalog = Catalog.load(tmp_path)
    # Homonyms must not merge identities, nor unknown enums become invented genders.
    assert catalog.npcs["1"]["name"] == catalog.npcs["2"]["name"] == "Même nom"
    assert (catalog.npcs["1"]["gender"], catalog.npcs["2"]["gender"]) == (0, 1)
    assert catalog.npcs["1"]["look"] != catalog.npcs["2"]["look"]
    assert catalog.npcs["3"]["name"] is None
    assert catalog.npcs["3"]["gender"] == 7
    assert catalog.npcs["1"]["dialogData"]["Array"][0]["moodId"] == 2
    assert (tmp_path / "catalog-before-metadata.json").read_bytes() == original
    migrated = target.read_bytes()
    prepare_network.prepare(tmp_path)
    assert target.read_bytes() == migrated
    assert (tmp_path / "catalog-before-metadata.json").read_bytes() == original


def test_custom_catalog_is_not_overwritten_or_backed_up_as_generated(tmp_path, monkeypatch):
    legacy = sources(tmp_path, monkeypatch)
    legacy["names"]["1"] = "Modification personnelle"
    target = tmp_path / "catalog.json"
    original = json.dumps(legacy).encode()
    target.write_bytes(original)
    with pytest.raises(RuntimeError, match="laissé intact"):
        prepare_network.prepare(tmp_path)
    assert target.read_bytes() == original
    assert not (tmp_path / "catalog-before-metadata.json").exists()
