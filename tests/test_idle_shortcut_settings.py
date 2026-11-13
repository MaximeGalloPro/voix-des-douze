"""Changing the idle default preserves custom bindings and the opt-out."""
import json
import pytest

from voix_des_douze.settings import Store


@pytest.mark.parametrize('old, expected', [(None, '='), ('F9', '='), ('F10', 'F10'), ('=', '=')])
def test_idle_default_migration_preserves_custom_binding_and_opt_out(tmp_path, old, expected):
    path = tmp_path / 'settings.json'
    shortcuts = {'toggle': 'F6', 'stop': 'F7', 'replay': 'F8'}
    if old is not None:
        shortcuts['idle'] = old
    path.write_text(json.dumps({'shortcuts': shortcuts, 'idle_shortcut_enabled': False}))
    store = Store(path)
    assert store.settings.shortcuts['idle'] == expected
    assert not store.settings.idle_shortcut_enabled
    store.save()
    reloaded = Store(path)
    assert reloaded.settings.shortcuts['idle'] == expected
    assert not reloaded.settings.idle_shortcut_enabled
