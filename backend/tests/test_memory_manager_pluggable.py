"""Pluggable memory manager: the factory resolves the configured backend.

Covers the drop-in contract end-to-end:
- short name -> registered backend (deermem / noop);
- dotted path (``module.Attr`` and ``module:Attr``) -> the same class;
- unknown value -> raise (fail-fast: a wrong store is a silent data-integrity footgun).

Also pins the noop empty-memory behaviour and the ``hasattr`` capability
probing surface (reload_memory + fact CRUD) that the gateway/client rely on.

Each test resets the singleton + backend cache and sets the config, so they
are independent of order.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(('enabled', 'expected'), [(True, 'high'), (False, 'none')])
def test_host_memory_model_preserves_saved_thinking_on_wire(monkeypatch, enabled, expected):
    """Exercise the real client: auxiliary extraction must not override the profile."""
    import json
    import httpx
    import deerflow.models.factory as factory
    from deerflow.config.app_config import AppConfig
    from deerflow.agents.memory.manager import _host_default_llm

    config = AppConfig.model_validate({'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'},
        'models': [{'name': 'saved', 'use': 'langchain_openai:ChatOpenAI', 'model': 'test',
            'base_url': 'https://model.test/v1', 'api_key': 'test-key', 'max_retries': 0,
            'supports_thinking': True, 'supports_reasoning_effort': True,
            'default_thinking_enabled': enabled,
            'when_thinking_enabled': {'reasoning_effort': 'high'},
            'when_thinking_disabled': {'reasoning_effort': 'none'}}]})
    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'memory', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{}'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(factory, 'get_app_config', lambda: config)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        import deerflow.models
        monkeypatch.setattr(deerflow.models, 'create_chat_model',
            lambda **kwargs: factory.create_chat_model(**kwargs, http_client=client))
        model = _host_default_llm()
        assert model is not None
        model.invoke('Extract memory')
    assert seen[0]['reasoning_effort'] == expected

from deerflow.agents.memory import (
    MemoryManager,
    get_memory_manager,
    reset_memory_manager,
)
from deerflow.agents.memory.backends.deermem.deer_mem import DeerMem
from deerflow.agents.memory.backends.noop.noop_manager import NoopMemoryManager
from deerflow.config.memory_config import MemoryConfig, get_memory_config, set_memory_config


@pytest.fixture(autouse=True)
def _isolate_memory_manager():
    """Reset the singleton + restore config around every test."""
    orig = get_memory_config()
    reset_memory_manager()
    yield
    set_memory_config(orig)
    reset_memory_manager()


@pytest.mark.parametrize(
    "manager_class, expected",
    [
        ("deermem", DeerMem),
        ("noop", NoopMemoryManager),
        ("deerflow.agents.memory.backends.deermem.deer_mem.DeerMem", DeerMem),
        ("deerflow.agents.memory.backends.deermem.deer_mem:DeerMem", DeerMem),
        ("deerflow.agents.memory.backends.noop.noop_manager.NoopMemoryManager", NoopMemoryManager),
        ("deerflow.agents.memory.backends.noop.noop_manager:NoopMemoryManager", NoopMemoryManager),
    ],
)
def test_resolves_configured_backend(manager_class: str, expected: type[MemoryManager]) -> None:
    set_memory_config(MemoryConfig(manager_class=manager_class))
    manager = get_memory_manager()
    assert isinstance(manager, expected)
    # singleton: a second call returns the same instance
    assert get_memory_manager() is manager


def test_unknown_backend_raises_instead_of_falling_back() -> None:
    """An unknown manager_class is a config error: raise, don't silently fall
    back to DeerMem (memory is persistent state -- a wrong store is a silent
    data-integrity footgun)."""
    set_memory_config(MemoryConfig(manager_class="bogus-backend"))
    with pytest.raises(ValueError, match="bogus-backend"):
        get_memory_manager()


def test_noop_runs_with_empty_memory() -> None:
    set_memory_config(MemoryConfig(manager_class="noop"))
    manager = get_memory_manager()
    assert manager.get_context(user_id="u") == ""
    assert manager.search("anything", user_id="owner") == []
    assert manager.get_memory(user_id="u") == {"facts": []}
    # writes are no-ops; memory stays empty
    manager.add("t", [], agent_name=None, user_id="u")
    manager.add_nowait("t", [], agent_name=None, user_id="u")
    assert manager.get_memory(user_id="u") == {"facts": []}


def test_internal_capabilities_are_hasattr_probeable() -> None:
    """reload_memory + fact CRUD + warm exist on DeerMem but not on noop (the ABC omits them)."""
    set_memory_config(MemoryConfig(manager_class="deermem"))
    deermem = get_memory_manager()
    for cap in ("warm", "reload_memory", "create_fact", "delete_fact", "update_fact"):
        assert hasattr(deermem, cap), cap

    reset_memory_manager()
    set_memory_config(MemoryConfig(manager_class="noop"))
    noop = get_memory_manager()
    for cap in ("warm", "reload_memory", "create_fact", "delete_fact", "update_fact"):
        assert not hasattr(noop, cap), cap


def test_deermem_search_works_delete_export_are_stubs() -> None:
    set_memory_config(MemoryConfig(manager_class="deermem"))
    deermem = get_memory_manager()
    # search is implemented (substring match) -- returns a list, does not raise.
    assert isinstance(deermem.search("q", user_id="u"), list)
    # delete_memory / export_memory remain unimplemented stubs this phase.
    with pytest.raises(NotImplementedError):
        deermem.delete_memory(user_id="u")
    with pytest.raises(NotImplementedError):
        deermem.export_memory(user_id="u")


def test_factory_raises_when_storage_path_is_existing_file(tmp_path) -> None:
    """A storage_path that resolves to an existing FILE is a config error: DeerMem
    treats storage_path as a root directory, so a file would make save's mkdir
    raise NotADirectoryError (silent write failure). Fail loud at startup (#1)."""
    file_path = tmp_path / "mem.json"
    file_path.write_text("{}", encoding="utf-8")
    set_memory_config(MemoryConfig(manager_class="deermem", backend_config={"storage_path": str(file_path)}))
    with pytest.raises(ValueError, match="existing file"):
        get_memory_manager()


def test_empty_storage_path_factory_injects_runtime_home(tmp_path, monkeypatch) -> None:
    """Empty/absent storage_path -> factory injects runtime_home() as the root, so
    per-user memory lands at {runtime_home}/users/{uid}/memory.json (matches
    current per-user location). Pins the zero-config default."""
    import deerflow.config.runtime_paths as rp

    monkeypatch.setattr(rp, "runtime_home", lambda: tmp_path)
    set_memory_config(MemoryConfig(manager_class="deermem"))  # no storage_path
    manager = get_memory_manager()
    assert Path(manager._config.storage_path) == tmp_path
    manager.create_fact("hello", user_id="u1")
    # per-user dir created under the injected runtime_home root
    user_dirs = [p.name for p in (tmp_path / "users").iterdir() if p.is_dir()]
    assert len(user_dirs) == 1
