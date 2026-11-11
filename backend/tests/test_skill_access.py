"""Selected Skill access narrows existing readers without replacing sandbox IO."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from deerflow.skills.access import bind_skill_read_tools
from deerflow.skills.types import Skill, SkillCategory


@pytest.fixture
def scoped_reader(tmp_path):
    root = tmp_path / "skills"
    directory = root / "public" / "reader"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("reader body", encoding="utf-8")
    sibling = root / "public" / "other"
    sibling.mkdir()
    (sibling / "SKILL.md").write_text("other body", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside body", encoding="utf-8")
    skill = Skill("reader", "Reader", None, directory, directory / "SKILL.md", Path("reader"), SkillCategory.PUBLIC, enabled=True)

    @tool("read_file", description="Read an existing sandbox path")
    def read_file(path: str) -> str:
        if path.startswith("/mnt/skills/"):
            path = str(root / path.removeprefix("/mnt/skills/"))
        return Path(path).read_text(encoding="utf-8")

    async def async_read(path: str) -> str:
        return read_file.func(path)
    read_file.coroutine = async_read
    app = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills", get_skills_path=lambda: root))
    bound = bind_skill_read_tools([read_file], [skill], app_config=app)[0]
    return bound, read_file, root, tmp_path


@pytest.mark.anyio
@pytest.mark.parametrize("path", [
    "/mnt/skills/public/other/SKILL.md",
    "/mnt/skills/public/reader/../other/SKILL.md",
    "/mnt/skills/public/reader/../../../outside.txt",
    "host-other", "host-escape",
])
async def test_scoped_reader_denies_unselected_and_traversal_in_both_paths(scoped_reader, path):
    bound, _, root, tmp_path = scoped_reader
    if path == "host-other":
        path = str(root / "public" / "other" / "SKILL.md")
    elif path == "host-escape":
        path = str(root / "public" / "reader" / ".." / ".." / ".." / "outside.txt")
    for result in [bound.invoke({"path": path}), await bound.ainvoke({"path": path})]:
        assert result.startswith("Error:")
        assert "body" not in result


@pytest.mark.anyio
async def test_scoped_reader_preserves_schema_allowed_reads_and_project_reads(scoped_reader):
    bound, original, root, tmp_path = scoped_reader
    assert bound.args_schema is original.args_schema
    assert original.func is not bound.func
    for path, expected in [
        ("/mnt/skills/public/reader/SKILL.md", "reader body"),
        (str(root / "public" / "reader" / "SKILL.md"), "reader body"),
        (str(tmp_path / "outside.txt"), "outside body"),
    ]:
        assert bound.invoke({"path": path}) == expected
        assert await bound.ainvoke({"path": path}) == expected


@pytest.mark.anyio
async def test_async_only_reader_keeps_the_same_skill_scope(scoped_reader):
    _, original, root, _ = scoped_reader
    async_only = original.model_copy(update={"func": None})
    app = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills", get_skills_path=lambda: root))
    bound = bind_skill_read_tools([async_only], [], app_config=app)[0]
    result = await bound.ainvoke({"path": "/mnt/skills/public/other/SKILL.md"})
    assert result.startswith("Error:") and "other body" not in result


@pytest.mark.parametrize("name", ["ls", "glob", "grep"])
def test_other_read_tools_cannot_bypass_skill_scope(scoped_reader, name):
    _, original, root, _ = scoped_reader
    reader = original.model_copy(update={"name": name})
    app = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills", get_skills_path=lambda: root))
    bound = bind_skill_read_tools([reader], [], app_config=app)[0]
    assert bound.invoke({"path": "/mnt/skills/public/other/SKILL.md"}).startswith("Error:")


def test_scoped_reader_blocks_supporting_resource_symlink_escape(scoped_reader):
    bound, _, root, tmp_path = scoped_reader
    link = root / "public" / "reader" / "link.txt"
    try:
        link.symlink_to(tmp_path / "outside.txt")
    except OSError:
        pytest.skip("Creating symlinks requires privileges on this host")
    assert bound.invoke({"path": "/mnt/skills/public/reader/link.txt"}).startswith("Error:")


def test_scoped_native_read_file_preserves_runtime_and_line_ranges(tmp_path, monkeypatch):
    from test_read_file_line_range import _local_runtime

    from deerflow.sandbox.local.local_sandbox import LocalSandbox
    from deerflow.sandbox.tools import read_file_tool

    runtime = _local_runtime(tmp_path)
    (tmp_path / "uploads" / "lines.txt").write_text("one\ntwo\nthree", encoding="utf-8")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    app = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills"))
    bound = bind_skill_read_tools([read_file_tool], [], app_config=app)[0]
    assert bound.func(runtime=runtime, description="Inspect", path="/mnt/user-data/uploads/lines.txt", start_line=2, end_line=2) == "two"


def test_skill_scope_uses_the_storage_bucket_for_external_user_identity(tmp_path, monkeypatch):
    from deerflow.config.paths import Paths, make_safe_user_id

    user_id = "oidc:user@example.com"
    paths = Paths(tmp_path)
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)
    root = paths.user_custom_skills_dir(make_safe_user_id(user_id))
    directory = root / "reader"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("custom body", encoding="utf-8")
    skill = Skill("reader", "Reader", None, directory, directory / "SKILL.md", Path("reader"), SkillCategory.CUSTOM, enabled=True)

    @tool("read_file", description="Read a host file")
    def reader(path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    app = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills"))
    allowed = bind_skill_read_tools([reader], [skill], app_config=app, user_id=user_id)[0]
    denied = bind_skill_read_tools([reader], [], app_config=app, user_id=user_id)[0]
    assert allowed.invoke({"path": str(directory / "SKILL.md")}) == "custom body"
    assert denied.invoke({"path": str(directory / "SKILL.md")}).startswith("Error:")
    manager = bind_skill_read_tools([reader], [], app_config=app, user_id=user_id, manage_custom=True)[0]
    assert manager.invoke({"path": str(directory / "SKILL.md")}) == 'custom body'


@pytest.fixture
def mounted_readers(tmp_path, monkeypatch):
    from test_read_file_line_range import _local_runtime

    from deerflow.sandbox import tools as sandbox_tools
    from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping

    runtime = _local_runtime(tmp_path)
    root = tmp_path / "workspace" / "skills"
    for name in ("reader", "other"):
        directory = root / "public" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"{name} BODY", encoding="utf-8")
    (tmp_path / "workspace" / "note.txt").write_text("project BODY", encoding="utf-8")
    directory = root / "public" / "reader"
    skill = Skill("reader", "Reader", None, directory, directory / "SKILL.md", Path("reader"), SkillCategory.PUBLIC, enabled=True)
    sandbox = LocalSandbox("t1", path_mappings=[PathMapping("/project-alias", str(tmp_path / "workspace"))])
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
    async def initialized(runtime):
        return sandbox
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized_async", initialized)
    monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime: None)
    app = SimpleNamespace(skills=SimpleNamespace(container_path="/mnt/skills", get_skills_path=lambda: root))
    originals = [sandbox_tools.read_file_tool, sandbox_tools.ls_tool, sandbox_tools.glob_tool, sandbox_tools.grep_tool]
    bound = {t.name: t for t in bind_skill_read_tools(originals, [skill], app_config=app)}
    return runtime, root, sandbox, app, bound


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["read_file", "ls", "glob", "grep"])
async def test_native_readers_deny_unselected_skill_through_workspace_alias(mounted_readers, name):
    runtime, _, _, _, bound = mounted_readers
    path = "/mnt/user-data/workspace/skills/public/other"
    arguments = dict(runtime=runtime, description="Inspect", path=path + "/SKILL.md" if name == "read_file" else path)
    if name in {"glob", "grep"}:
        arguments["pattern"] = "**/*" if name == "glob" else "BODY"
    for result in [bound[name].func(**arguments), await bound[name].coroutine(**arguments)]:
        assert result.startswith("Error:"), result
        assert "other BODY" not in result


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["glob", "grep"])
async def test_recursive_project_search_does_not_expose_unselected_skills(mounted_readers, name):
    runtime, _, _, _, bound = mounted_readers
    arguments = dict(runtime=runtime, description="Search", path="/mnt/user-data/workspace", pattern="**/*" if name == "glob" else "BODY")
    for result in [bound[name].func(**arguments), await bound[name].coroutine(**arguments)]:
        assert "other" not in result
        assert "note.txt" in result
        assert "reader" in result


@pytest.mark.anyio
async def test_allowed_alias_reads_preserve_project_access_and_do_not_leak_policy(mounted_readers):
    runtime, root, sandbox, _, bound = mounted_readers
    for suffix, expected in [("skills/public/reader/SKILL.md", "reader BODY"), ("note.txt", "project BODY")]:
        arguments = dict(runtime=runtime, description="Read", path="/mnt/user-data/workspace/" + suffix)
        assert bound["read_file"].func(**arguments) == expected
        assert await bound["read_file"].coroutine(**arguments) == expected
    assert sandbox.read_file(str(root / "public" / "other" / "SKILL.md")) == "other BODY"


@pytest.mark.anyio
async def test_parallel_readers_keep_separate_scopes_on_one_sandbox(mounted_readers):
    import asyncio

    _, root, sandbox, app, _ = mounted_readers
    entered = 0
    both_entered = asyncio.Event()

    @tool("read_file", description="Read through the configured sandbox mount")
    def reader(path: str) -> str:
        return sandbox.read_file(path)

    async def simultaneous_read(path: str) -> str:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        return await asyncio.to_thread(sandbox.read_file, path)

    reader.coroutine = simultaneous_read
    directory = root / "public" / "reader"
    skill = Skill("reader", "Reader", None, directory, directory / "SKILL.md", Path("reader"), SkillCategory.PUBLIC, enabled=True)
    allowed = bind_skill_read_tools([reader], [skill], app_config=app)[0]
    denied = bind_skill_read_tools([reader], [], app_config=app)[0]
    path = "/project-alias/skills/public/reader/SKILL.md"
    results = await asyncio.wait_for(asyncio.gather(allowed.ainvoke({"path": path}), denied.ainvoke({"path": path}), return_exceptions=True), timeout=5)
    assert results[0] == "reader BODY"
    assert isinstance(results[1], PermissionError)
    assert sandbox.read_file(path) == "reader BODY"


def test_symlink_alias_does_not_bypass_selected_skill_scope(mounted_readers, tmp_path):
    runtime, root, _, _, bound = mounted_readers
    link = tmp_path / "workspace" / "alias.txt"
    try:
        link.symlink_to(root / "public" / "other" / "SKILL.md")
    except OSError:
        pytest.skip("Creating symlinks requires privileges on this host")
    result = bound["read_file"].func(runtime=runtime, description="Read", path="/mnt/user-data/workspace/alias.txt")
    assert result.startswith("Error:") and "other BODY" not in result
