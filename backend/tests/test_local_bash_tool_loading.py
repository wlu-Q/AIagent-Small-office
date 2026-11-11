from types import SimpleNamespace

from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.tools.tools import get_available_tools, get_runtime_tool_inventory


def _make_config(*, allow_host_bash: bool, sandbox_use: str = "deerflow.sandbox.local:LocalSandboxProvider", extra_tools: list[SimpleNamespace] | None = None):
    return SimpleNamespace(
        tools=[
            SimpleNamespace(name="bash", group="bash", use="deerflow.sandbox.tools:bash_tool"),
            SimpleNamespace(name="ls", group="file:read", use="tests:ls_tool"),
            *(extra_tools or []),
        ],
        models=[],
        sandbox=SimpleNamespace(
            use=sandbox_use,
            allow_host_bash=allow_host_bash,
        ),
        tool_search=SimpleNamespace(enabled=False),
        get_model_config=lambda name: None,
    )


def test_get_available_tools_hides_bash_for_default_local_sandbox(monkeypatch):
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: _make_config(allow_host_bash=False))
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" not in names
    assert "ls" in names


def test_get_available_tools_keeps_bash_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: _make_config(allow_host_bash=True))
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" in names
    assert "ls" in names


def test_get_available_tools_hides_renamed_host_bash_alias(monkeypatch):
    config = _make_config(
        allow_host_bash=False,
        extra_tools=[SimpleNamespace(name="shell", group="bash", use="deerflow.sandbox.tools:bash_tool")],
    )
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: config)
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash_tool" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" not in names
    assert "shell" not in names
    assert "ls" in names


def test_get_available_tools_keeps_bash_for_aio_sandbox(monkeypatch):
    config = _make_config(
        allow_host_bash=False,
        sandbox_use="deerflow.community.aio_sandbox:AioSandboxProvider",
    )
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: config)
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash_tool" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" in names
    assert "ls" in names


def test_is_host_bash_allowed_defaults_false_when_sandbox_missing():
    assert is_host_bash_allowed(SimpleNamespace()) is False
    assert is_host_bash_allowed(SimpleNamespace(sandbox=None)) is False


def test_runtime_tool_inventory_distinguishes_authorized_and_available_groups(monkeypatch):
    config = _make_config(allow_host_bash=False)
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash" in use else "ls"),
    )

    inventory = get_runtime_tool_inventory(
        groups=["bash", "file:read"],
        include_mcp=False,
        app_config=config,
    )

    assert inventory["authorized_tool_groups"] == ["bash", "file:read"]
    assert inventory["available_tool_groups"] == ["file:read"]
    assert inventory["unavailable_tool_groups"] == ["bash"]
    assert 'ls' in inventory['available_tool_names']
    assert 'bash' not in inventory['available_tool_names']
    assert 'ask_clarification' in inventory['available_tool_names']
    assert inventory["unavailable_reasons"] == {"bash": "host_bash_disabled"}
    assert inventory["available_tool_group_members"] == {"file:read": ["ls"]}


def test_runtime_tool_inventory_maps_available_groups_to_member_tool_names(monkeypatch):
    config = _make_config(
        allow_host_bash=False,
        extra_tools=[
            # 声明顺序刻意与字母序不同，锁定「按配置顺序」而非排序输出
            SimpleNamespace(name="web_search", group="web", use="tests:web_search_tool"),
            SimpleNamespace(name="web_fetch", group="web", use="tests:web_fetch_tool"),
            SimpleNamespace(name="read_file", group="file:read", use="tests:read_file_tool"),
        ],
    )
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name=use.rsplit(":", 1)[-1].removesuffix("_tool")),
    )

    inventory = get_runtime_tool_inventory(
        groups=["bash", "file:read", "web"],
        include_mcp=False,
        app_config=config,
    )

    assert inventory["available_tool_groups"] == ["file:read", "web"]
    assert inventory["available_tool_group_members"] == {
        "file:read": ["ls", "read_file"],
        "web": ["web_search", "web_fetch"],
    }
