from PIL import Image
from voix_des_douze.dialogue import DialogueHistory, ImageGate, same_dialogue
from voix_des_douze.settings import Store


def test_duplicate_new_npc_closed_reopened():
    history = DialogueHistory()
    assert history.accept("Kerubim", "Bonjour aventurier !")
    assert not history.accept("Kerubim", "Bonjour aventurier.")
    assert history.accept("Nacf", "Bonjour aventurier !")
    assert not history.accept("Nacf", "Bonjour aventurier !")
    assert not history.accept("", "")
    assert history.accept("Nacf", "Bonjour aventurier !")


def test_distinct_short_choices_numbers_and_negation():
    assert not same_dialogue("Oui.", "Non.")
    assert not same_dialogue("Vous avez 12 kamas.", "Vous avez 13 kamas.")
    assert not same_dialogue("Je peux partir.", "Je ne peux partir.")
    prefix = "Nous vous attendons près de la fontaine du village pour commencer notre voyage. "
    assert same_dialogue(prefix + "Bonjour aventurier.", prefix + "Bonjour aventurler.")
    assert not same_dialogue(prefix + "Revenez demain.", prefix + "Revenez bientôt.")


def test_visual_stability_no_repeat_then_changed():
    gate = ImageGate(delay=0.3)
    white, black = Image.new("RGB", (200, 100), "white"), Image.new("RGB", (200, 100), "black")
    assert gate.observe(white, 0) == (True, False)
    assert gate.observe(white, 0.2) == (False, False)
    assert gate.observe(white, 0.4) == (False, True)
    assert gate.observe(white, 10) == (False, False)
    assert gate.observe(black, 11) == (True, False)
    assert gate.observe(black, 11.4) == (False, True)


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
