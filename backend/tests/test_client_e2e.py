"""End-to-end tests for DeerFlowClient with isolated files and configuration."""

import json
import uuid
import zipfile
from pathlib import Path

import pytest

from deerflow.client import DeerFlowClient
from deerflow.config.app_config import AppConfig

def _make_e2e_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "models": [
                {
                    "name": "e2e-test",
                    "display_name": "E2E Test Model",
                    "use": "langchain_openai:ChatOpenAI",
                    "model": "e2e-test",
                    "supports_thinking": False,
                }
            ],
            "sandbox": {
                "use": "deerflow.sandbox.local:LocalSandboxProvider",
                "allow_host_bash": True,
            },
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def e2e_env(tmp_path, monkeypatch):
    """Isolated filesystem environment for E2E tests.

    - DEER_FLOW_HOME → tmp_path (all thread data lands in a temp dir)
    - DEER_FLOW_PROJECT_ROOT → repository root (shared skills/config assets
      still resolve correctly when tests run from backend/)
    - Singletons reset so they pick up the new env
    - Title/memory/summarization disabled to avoid extra LLM calls
    - AppConfig built programmatically (avoids config.yaml param-name issues)
    """
    # 1. Filesystem isolation
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setenv(
        "DEER_FLOW_PROJECT_ROOT",
        str(Path(__file__).resolve().parents[2]),
    )
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider._default_sandbox_provider", None)

    # 2. Inject a clean AppConfig. We must reset _app_config to None BEFORE
    # calling _make_e2e_config() because AppConfig() constructor misbehaves when
    # a disk config is already cached: it returns the cached model list instead
    # of the provided one. Clearing first ensures the test config is correct.
    monkeypatch.setattr("deerflow.config.app_config._app_config", None)
    monkeypatch.setattr("deerflow.config.app_config._app_config_is_custom", False)
    config = _make_e2e_config()
    monkeypatch.setattr("deerflow.config.app_config._app_config", config)
    monkeypatch.setattr("deerflow.config.app_config._app_config_is_custom", True)
    monkeypatch.setattr("deerflow.client.get_app_config", lambda: config)

    # 3. Disable title generation (extra LLM call, non-deterministic)
    from deerflow.config.title_config import TitleConfig

    monkeypatch.setattr("deerflow.config.title_config._title_config", TitleConfig(enabled=False))

    # 5. Ensure summarization is off (default, but be explicit)
    from deerflow.config.summarization_config import SummarizationConfig

    monkeypatch.setattr("deerflow.config.summarization_config._summarization_config", SummarizationConfig(enabled=False))

    # 6. Exclude TitleMiddleware from the chain.
    #    It triggers an extra LLM call to generate a thread title, which adds
    #    non-determinism and cost to E2E tests (title generation is already
    #    disabled via TitleConfig above, but the middleware still participates
    #    in the chain and can interfere with event ordering).
    from deerflow.agents.lead_agent.agent import build_middlewares as _original_build_middlewares
    from deerflow.agents.middlewares.title_middleware import TitleMiddleware

    def _sync_safe_build_middlewares(*args, **kwargs):
        mws = _original_build_middlewares(*args, **kwargs)
        return [m for m in mws if not isinstance(m, TitleMiddleware)]

    monkeypatch.setattr("deerflow.client.build_middlewares", _sync_safe_build_middlewares)

    return {"tmp_path": tmp_path}


class TestFileUploadIntegration:
    """Upload, list, and delete files through the real client path."""

    def test_upload_files(self, e2e_env, tmp_path):
        """upload_files() copies files and returns metadata."""
        test_file = tmp_path / "source" / "readme.txt"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("Hello world")

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        tid = str(uuid.uuid4())

        result = c.upload_files(tid, [test_file])
        assert result["success"] is True
        assert len(result["files"]) == 1
        assert result["files"][0]["filename"] == "readme.txt"

        # Physically exists
        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        assert (get_paths().sandbox_uploads_dir(tid, user_id=get_effective_user_id()) / "readme.txt").exists()

    def test_upload_duplicate_rename(self, e2e_env, tmp_path):
        """Uploading two files with the same name auto-renames the second."""
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "data.txt").write_text("content A")
        (d2 / "data.txt").write_text("content B")

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        tid = str(uuid.uuid4())

        result = c.upload_files(tid, [d1 / "data.txt", d2 / "data.txt"])
        assert result["success"] is True
        assert len(result["files"]) == 2

        filenames = {f["filename"] for f in result["files"]}
        assert "data.txt" in filenames
        assert "data_1.txt" in filenames

    def test_upload_list_and_delete(self, e2e_env, tmp_path):
        """Upload → list → delete → list lifecycle."""
        test_file = tmp_path / "lifecycle.txt"
        test_file.write_text("lifecycle test")

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        tid = str(uuid.uuid4())

        c.upload_files(tid, [test_file])

        listing = c.list_uploads(tid)
        assert listing["count"] == 1
        assert listing["files"][0]["filename"] == "lifecycle.txt"

        del_result = c.delete_upload(tid, "lifecycle.txt")
        assert del_result["success"] is True

        listing = c.list_uploads(tid)
        assert listing["count"] == 0


class TestLifecycleAndConfig:
    """Agent recreation and configuration behavior."""

    def test_reset_agent_clears_state(self, e2e_env):
        """reset_agent() sets the internal agent to None."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        # Before any call, agent is None
        assert c._agent is None

        c.reset_agent()
        assert c._agent is None
        assert c._agent_config_key is None

    def test_plan_mode_config_key(self, e2e_env):
        """plan_mode is part of the config key tuple."""
        c = DeerFlowClient(checkpointer=None, plan_mode=False)
        cfg1 = c._get_runnable_config("test-thread")
        key1 = (
            cfg1["context"]["model_name"],
            cfg1["context"]["thinking_enabled"],
            cfg1["context"]["is_plan_mode"],
            cfg1["context"]["subagent_enabled"],
        )

        c2 = DeerFlowClient(checkpointer=None, plan_mode=True)
        cfg2 = c2._get_runnable_config("test-thread")
        key2 = (
            cfg2["context"]["model_name"],
            cfg2["context"]["thinking_enabled"],
            cfg2["context"]["is_plan_mode"],
            cfg2["context"]["subagent_enabled"],
        )

        assert cfg1["configurable"] == {"thread_id": "test-thread"}
        assert cfg2["configurable"] == {"thread_id": "test-thread"}
        assert key1 != key2
        assert key1[2] is False
        assert key2[2] is True


class TestErrorAndBoundary:
    """Error propagation and edge cases."""

    def test_upload_nonexistent_file_raises(self, e2e_env):
        """Uploading a file that doesn't exist raises FileNotFoundError."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(FileNotFoundError):
            c.upload_files("test-thread", ["/nonexistent/file.txt"])

    def test_delete_nonexistent_upload_raises(self, e2e_env):
        """Deleting a file that doesn't exist raises FileNotFoundError."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        tid = str(uuid.uuid4())
        # Ensure the uploads dir exists first
        c.list_uploads(tid)
        with pytest.raises(FileNotFoundError):
            c.delete_upload(tid, "ghost.txt")

    def test_artifact_path_traversal_blocked(self, e2e_env):
        """get_artifact blocks path traversal attempts."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(ValueError):
            c.get_artifact("test-thread", "../../etc/passwd")

    def test_upload_directory_rejected(self, e2e_env, tmp_path):
        """Uploading a directory (not a file) is rejected."""
        d = tmp_path / "a_directory"
        d.mkdir()
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(ValueError, match="not a file"):
            c.upload_files("test-thread", [d])


class TestArtifactAccess:
    """Read artifacts through get_artifact() with real filesystem."""

    def test_get_artifact_happy_path(self, e2e_env):
        """Write a file to outputs, then read it back via get_artifact()."""
        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        tid = str(uuid.uuid4())

        # Create an output file in the thread's outputs directory
        outputs_dir = get_paths().sandbox_outputs_dir(tid, user_id=get_effective_user_id())
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "result.txt").write_text("hello artifact")

        data, mime = c.get_artifact(tid, "mnt/user-data/outputs/result.txt")
        assert data == b"hello artifact"
        assert "text" in mime

    def test_get_artifact_nested_path(self, e2e_env):
        """Artifacts in subdirectories are accessible."""
        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        tid = str(uuid.uuid4())

        outputs_dir = get_paths().sandbox_outputs_dir(tid, user_id=get_effective_user_id())
        sub = outputs_dir / "charts"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "data.json").write_text('{"x": 1}')

        data, mime = c.get_artifact(tid, "mnt/user-data/outputs/charts/data.json")
        assert b'"x"' in data
        assert "json" in mime

    def test_get_artifact_nonexistent_raises(self, e2e_env):
        """Reading a nonexistent artifact raises FileNotFoundError."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(FileNotFoundError):
            c.get_artifact("test-thread", "mnt/user-data/outputs/ghost.txt")

    def test_get_artifact_traversal_within_prefix_blocked(self, e2e_env):
        """Path traversal within the valid prefix is still blocked."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises((PermissionError, ValueError, FileNotFoundError)):
            c.get_artifact("test-thread", "mnt/user-data/outputs/../../etc/passwd")


class TestSkillInstallation:
    """install_skill() with real ZIP handling and filesystem."""

    @pytest.fixture(autouse=True)
    def _allow_skill_security_scan(self, monkeypatch):
        async def _scan(*args, **kwargs):
            from deerflow.skills.security_scanner import ScanResult

            return ScanResult(decision="allow", reason="ok")

        monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)

    @pytest.fixture(autouse=True)
    def _isolate_skills_dir(self, tmp_path, monkeypatch):
        """Redirect skill installation to a temp directory."""
        skills_root = tmp_path / "skills"
        (skills_root / "public").mkdir(parents=True)
        (skills_root / "custom").mkdir(parents=True)
        from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

        local_storage = LocalSkillStorage(host_path=str(skills_root))
        monkeypatch.setattr(
            "deerflow.skills.storage._default_skill_storage",
            local_storage,
        )
        monkeypatch.setattr(
            "deerflow.client.get_or_new_user_skill_storage",
            lambda user_id, **kwargs: local_storage,
        )
        self._skills_root = skills_root

    @staticmethod
    def _make_skill_zip(tmp_path, skill_name="test-e2e-skill"):
        """Create a minimal valid .skill archive."""
        skill_dir = tmp_path / "build" / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\ndescription: E2E test skill\n---\n\nTest content.\n")
        archive_path = tmp_path / f"{skill_name}.skill"
        with zipfile.ZipFile(archive_path, "w") as zf:
            for file in skill_dir.rglob("*"):
                zf.write(file, file.relative_to(tmp_path / "build"))
        return archive_path

    def test_install_skill_success(self, e2e_env, tmp_path):
        """A valid .skill archive installs to the custom skills directory."""
        archive = self._make_skill_zip(tmp_path)
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)

        result = c.install_skill(archive)
        assert result["success"] is True
        assert result["skill_name"] == "test-e2e-skill"
        assert (self._skills_root / "custom" / "test-e2e-skill" / "SKILL.md").exists()

    def test_install_skill_duplicate_rejected(self, e2e_env, tmp_path):
        """Installing the same skill twice raises ValueError."""
        archive = self._make_skill_zip(tmp_path)
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)

        c.install_skill(archive)
        with pytest.raises(ValueError, match="already exists"):
            c.install_skill(archive)

    def test_install_skill_invalid_extension(self, e2e_env, tmp_path):
        """A file without .skill extension is rejected."""
        bad_file = tmp_path / "not_a_skill.zip"
        bad_file.write_bytes(b"PK\x03\x04")  # ZIP magic bytes
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(ValueError, match=".skill extension"):
            c.install_skill(bad_file)

    def test_install_skill_missing_frontmatter(self, e2e_env, tmp_path):
        """A .skill archive without valid SKILL.md frontmatter is rejected."""
        skill_dir = tmp_path / "build" / "bad-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("No frontmatter here.")

        archive = tmp_path / "bad-skill.skill"
        with zipfile.ZipFile(archive, "w") as zf:
            for file in skill_dir.rglob("*"):
                zf.write(file, file.relative_to(tmp_path / "build"))

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(ValueError, match="Invalid skill"):
            c.install_skill(archive)

    def test_install_skill_nonexistent_file(self, e2e_env):
        """Installing from a nonexistent path raises FileNotFoundError."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(FileNotFoundError):
            c.install_skill("/nonexistent/skill.skill")


class TestConfigManagement:
    """Config queries and updates through real code paths."""

    def test_list_models_returns_injected_config(self, e2e_env):
        """list_models() returns the model from the injected AppConfig."""
        expected_model_name = "e2e-test"
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.list_models()
        assert "models" in result
        assert len(result["models"]) == 1
        assert result["models"][0]["name"] == expected_model_name
        assert result["models"][0]["display_name"] == "E2E Test Model"

    def test_get_model_found(self, e2e_env):
        """get_model() returns the model when it exists."""
        expected_model_name = "e2e-test"
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        model = c.get_model(expected_model_name)
        assert model is not None
        assert model["name"] == expected_model_name
        assert model["supports_thinking"] is False

    def test_get_model_not_found(self, e2e_env):
        """get_model() returns None for nonexistent model."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        assert c.get_model("nonexistent-model") is None

    def test_list_skills_returns_list(self, e2e_env):
        """list_skills() returns a dict with 'skills' key from real directory scan."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.list_skills()
        assert "skills" in result
        assert isinstance(result["skills"], list)
        # The real skills/ directory should have some public skills
        assert len(result["skills"]) > 0

    def test_get_skill_found(self, e2e_env):
        """get_skill() returns skill info for a known public skill."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        # 'deep-research' is a built-in public skill
        skill = c.get_skill("deep-research")
        if skill is not None:
            assert skill["name"] == "deep-research"
            assert "description" in skill
            assert "enabled" in skill

    def test_get_skill_not_found(self, e2e_env):
        """get_skill() returns None for nonexistent skill."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        assert c.get_skill("nonexistent-skill-xyz") is None

    def test_get_mcp_config_returns_dict(self, e2e_env):
        """get_mcp_config() returns a dict with 'mcp_servers' key."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.get_mcp_config()
        assert "mcp_servers" in result
        assert isinstance(result["mcp_servers"], dict)

    def test_update_mcp_config_writes_and_invalidates(self, e2e_env, tmp_path, monkeypatch):
        """update_mcp_config() writes extensions_config.json and invalidates the agent."""
        # Set up a writable extensions_config.json
        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(json.dumps({"mcpServers": {}, "skills": {}}))
        monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(config_file))

        # Force reload so the singleton picks up our test file
        from deerflow.config.extensions_config import reload_extensions_config

        reload_extensions_config()

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        # Simulate a cached agent
        c._agent = "fake-agent-placeholder"
        c._agent_config_key = ("a", "b", "c", "d")

        result = c.update_mcp_config({"test-server": {"enabled": True, "type": "stdio", "command": "echo"}})
        assert "mcp_servers" in result

        # Agent should be invalidated
        assert c._agent is None
        assert c._agent_config_key is None

        # File should be written
        written = json.loads(config_file.read_text())
        assert "test-server" in written["mcpServers"]

    def test_update_skill_writes_and_invalidates(self, e2e_env, tmp_path, monkeypatch):
        """update_skill() writes extensions_config.json and invalidates the agent."""
        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(json.dumps({"mcpServers": {}, "skills": {}}))
        monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(config_file))

        from deerflow.config.extensions_config import reload_extensions_config

        reload_extensions_config()

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        c._agent = "fake-agent-placeholder"
        c._agent_config_key = ("a", "b", "c", "d")

        # Use a real skill name from the public skills directory
        skills = c.list_skills()
        if not skills["skills"]:
            pytest.skip("No skills available for testing")
        skill_name = skills["skills"][0]["name"]

        result = c.update_skill(skill_name, enabled=False)
        assert result["name"] == skill_name
        assert result["enabled"] is False

        # Agent should be invalidated
        assert c._agent is None
        assert c._agent_config_key is None

    def test_update_skill_nonexistent_raises(self, e2e_env, tmp_path, monkeypatch):
        """update_skill() raises ValueError for nonexistent skill."""
        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(json.dumps({"mcpServers": {}, "skills": {}}))
        monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(config_file))

        from deerflow.config.extensions_config import reload_extensions_config

        reload_extensions_config()

        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        with pytest.raises(ValueError, match="not found"):
            c.update_skill("nonexistent-skill-xyz", enabled=True)


class TestMemoryAccess:
    """Memory system queries through real code paths."""

    def test_get_memory_returns_dict(self, e2e_env):
        """get_memory() returns a dict (may be empty initial state)."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.get_memory()
        assert isinstance(result, dict)

    def test_reload_memory_returns_dict(self, e2e_env):
        """reload_memory() forces reload and returns a dict."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.reload_memory()
        assert isinstance(result, dict)

    def test_get_memory_config_fields(self, e2e_env):
        """get_memory_config() returns expected config fields."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.get_memory_config()
        assert "enabled" in result
        assert "injection_enabled" in result
        assert "manager_class" in result
        assert "backend_config" in result
        assert "mode" in result

    def test_get_memory_status_combines_config_and_data(self, e2e_env):
        """get_memory_status() returns both 'config' and 'data' keys."""
        c = DeerFlowClient(checkpointer=None, thinking_enabled=False)
        result = c.get_memory_status()
        assert "config" in result
        assert "data" in result
        assert "enabled" in result["config"]
        assert "mode" in result["config"]
        assert isinstance(result["data"], dict)
