"""End-to-end checks for public and current-user skill mounts."""

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deerflow.config.paths import Paths
from deerflow.sandbox.local.local_sandbox import PathMapping
from deerflow.sandbox.local.local_sandbox_provider import LocalSandboxProvider
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from deerflow.skills.types import SKILL_MD_FILE, Skill, SkillCategory

_AIO_MODULE = "deerflow.community.aio_sandbox.aio_sandbox_provider"
_AIO_GET_CONFIG = f"{_AIO_MODULE}.get_app_config"


def _write_skill(base: Path, name: str, description: str) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / SKILL_MD_FILE
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_md


def _build_config(skills_root: Path):
    from deerflow.config.sandbox_config import SandboxConfig

    return SimpleNamespace(
        skills=SimpleNamespace(
            container_path="/mnt/skills",
            get_skills_path=lambda: skills_root,
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        ),
        sandbox=SandboxConfig(
            use="deerflow.sandbox.local:LocalSandboxProvider",
            mounts=[],
        ),
    )


def _local_mounts(provider: LocalSandboxProvider, thread_id: str, user_id: str) -> dict[str, PathMapping]:
    mappings = list(provider._path_mappings) + provider._build_thread_path_mappings(thread_id, user_id=user_id)
    return {mapping.container_path: mapping for mapping in mappings}


@pytest.fixture
def skills_fs(tmp_path: Path) -> dict[str, Path]:
    skills_root = tmp_path / "skills"
    public_root = skills_root / "public"
    user_custom_root = tmp_path / "users" / "user-1" / "skills" / "custom"
    return {
        "root": skills_root,
        "public": public_root,
        "user_custom": user_custom_root,
        "users_dir": tmp_path / "users",
        "public_skill": _write_skill(public_root, "public-skill", "public skill"),
        "user_skill": _write_skill(user_custom_root, "user-skill", "user custom skill"),
    }


@pytest.fixture
def aio_mod():
    return importlib.import_module(_AIO_MODULE)


class TestUserScopedSkillMounts:
    def test_local_mounts_public_and_current_user_custom(self, skills_fs):
        config = _build_config(skills_fs["root"])
        paths = Paths(base_dir=skills_fs["users_dir"].parent)
        with patch("deerflow.config.get_app_config", return_value=config), patch(
            "deerflow.config.paths.get_paths", return_value=paths
        ):
            provider = LocalSandboxProvider()
            mounts = _local_mounts(provider, "thread-1", user_id="user-1")

        assert mounts["/mnt/skills/public"].read_only is True
        assert mounts["/mnt/skills/public"].local_path == str(skills_fs["public"])
        assert mounts["/mnt/skills/custom"].read_only is False
        assert mounts["/mnt/skills/custom"].local_path == str(skills_fs["user_custom"])

    def test_local_sandbox_reads_public_and_current_user_custom(self, skills_fs):
        config = _build_config(skills_fs["root"])
        paths = Paths(base_dir=skills_fs["users_dir"].parent)
        with patch("deerflow.config.get_app_config", return_value=config), patch(
            "deerflow.config.paths.get_paths", return_value=paths
        ):
            provider = LocalSandboxProvider()
            sandbox_id = provider.acquire("thread-1", user_id="user-1")

        sandbox = provider.get(sandbox_id)
        assert sandbox is not None
        assert "public-skill" in sandbox.read_file("/mnt/skills/public/public-skill/SKILL.md")
        assert "user-skill" in sandbox.read_file("/mnt/skills/custom/user-skill/SKILL.md")

    def test_storage_category_to_local_sandbox_pipeline(self, skills_fs):
        config = _build_config(skills_fs["root"])
        paths = Paths(base_dir=skills_fs["users_dir"].parent)
        with patch("deerflow.config.get_app_config", return_value=config), patch(
            "deerflow.config.paths.get_paths", return_value=paths
        ):
            provider = LocalSandboxProvider()
            sandbox_id = provider.acquire("thread-1", user_id="user-1")
            storage = UserScopedSkillStorage(user_id="user-1", host_path=str(skills_fs["root"]))

        sandbox = provider.get(sandbox_id)
        assert sandbox is not None
        skills = {skill.name: skill for skill in storage.load_skills(enabled_only=False)}
        assert set(skills) == {"public-skill", "user-skill"}
        assert skills["public-skill"].category == SkillCategory.PUBLIC
        assert skills["user-skill"].category == SkillCategory.CUSTOM
        assert "public-skill" in sandbox.read_file(skills["public-skill"].get_container_file_path())
        assert "user-skill" in sandbox.read_file(skills["user-skill"].get_container_file_path())

    def test_aio_mounts_public_and_current_user_custom(self, skills_fs, aio_mod, monkeypatch):
        config = _build_config(skills_fs["root"])
        paths = Paths(base_dir=skills_fs["users_dir"].parent)
        monkeypatch.setattr(aio_mod, "get_paths", lambda: paths)
        with patch(_AIO_GET_CONFIG, return_value=config):
            mounts = aio_mod.AioSandboxProvider._get_skills_mounts(user_id="user-1")

        by_container = {container: (host, read_only) for host, container, read_only in mounts}
        assert by_container["/mnt/skills/public"] == (str(skills_fs["public"]), True)
        custom_host, custom_read_only = by_container["/mnt/skills/custom"]
        assert custom_read_only is True
        assert "users/user-1/skills/custom" in custom_host.replace("\\", "/")

    def test_aio_extra_mounts_translate_to_docker_bind_mounts(self, skills_fs, aio_mod, monkeypatch):
        from deerflow.community.aio_sandbox.local_backend import _format_container_mount

        config = _build_config(skills_fs["root"])
        paths = Paths(base_dir=skills_fs["users_dir"].parent)
        monkeypatch.setattr(aio_mod, "get_paths", lambda: paths)
        with patch(_AIO_GET_CONFIG, return_value=config):
            mounts = aio_mod.AioSandboxProvider._get_extra_mounts(
                aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider),
                "thread-1",
                user_id="user-1",
            )

        docker_mounts = {}
        for host, container, read_only in mounts:
            args = _format_container_mount("docker", host, container, read_only)
            if args[0] == "--mount":
                docker_mounts[container] = args[1]

        assert "dst=/mnt/skills/public" in docker_mounts["/mnt/skills/public"]
        assert "readonly" in docker_mounts["/mnt/skills/public"]
        assert "dst=/mnt/skills/custom" in docker_mounts["/mnt/skills/custom"]
        assert "users/user-1/skills/custom" in docker_mounts["/mnt/skills/custom"].replace("\\", "/")

    def test_skill_container_paths_match_current_mounts(self, skills_fs):
        public_skill = Skill(
            name="public-skill",
            description="",
            license=None,
            skill_dir=skills_fs["public"] / "public-skill",
            skill_file=skills_fs["public_skill"],
            relative_path=Path("public-skill"),
            category=SkillCategory.PUBLIC,
        )
        custom_skill = Skill(
            name="user-skill",
            description="",
            license=None,
            skill_dir=skills_fs["user_custom"] / "user-skill",
            skill_file=skills_fs["user_skill"],
            relative_path=Path("user-skill"),
            category=SkillCategory.CUSTOM,
        )
        assert public_skill.get_container_path() == "/mnt/skills/public/public-skill"
        assert custom_skill.get_container_path() == "/mnt/skills/custom/user-skill"
