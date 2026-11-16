import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from office_library import config
from office_library.service import LibraryService


@pytest.fixture
def keychain(monkeypatch):
    values = {}
    backend = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
    )
    module = ModuleType('keyring.backends.macOS')
    module.Keyring = lambda: backend
    monkeypatch.setitem(sys.modules, 'keyring.backends.macOS', module)
    monkeypatch.setattr(config, 'sys', SimpleNamespace(platform='darwin'))
    return backend, values


def test_keychain_encrypts_each_key_and_reopens_without_environment(tmp_path, keychain, monkeypatch):
    monkeypatch.delenv('OFFICE_LIBRARY_EMBEDDING_API_KEY', raising=False)
    monkeypatch.delenv('OFFICE_LIBRARY_RERANK_API_KEY', raising=False)
    service = LibraryService(tmp_path / 'library')
    service.save_settings({'embedding_api_key': 'embedding-test', 'rerank_api_key': 'rerank-test'})
    reopened = LibraryService(service.root)
    assert reopened._config()['embedding_api_key'] == 'embedding-test'
    assert reopened._config()['rerank_api_key'] == 'rerank-test'
    reopened.save_settings({**reopened.settings(), 'rerank_api_key': 'updated'})
    assert service._config()['embedding_api_key'] == 'embedding-test'
    assert service._config()['rerank_api_key'] == 'updated'
    with service.db() as db:
        saved = db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0]
    assert 'embedding-test' not in saved and 'updated' not in saved
    assert len(keychain[1]) == 1
    with pytest.raises(ValueError):
        config.decrypt_config(json.loads(saved), scope=tmp_path / 'other')
    assert len(keychain[1]) == 1


def test_denied_keychain_does_not_change_saved_config(tmp_path, keychain):
    service = LibraryService(tmp_path / 'library')
    service.save_settings({'embedding_api_key': 'old', 'rerank_api_key': 'old-rerank'})
    with service.db() as db:
        before = db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0]
    def denied(*args):
        raise RuntimeError('Access denied')
    keychain[0].get_password = denied
    with pytest.raises(ValueError):
        service.save_settings({'embedding_api_key': 'new', 'rerank_api_key': 'new'})
    with service.db() as db:
        assert db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0] == before
