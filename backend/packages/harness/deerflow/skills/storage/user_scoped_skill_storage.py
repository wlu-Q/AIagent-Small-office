"""User-scoped skill storage.

Public skills are read from ``{base_dir}/skills/public/``. Custom skills,
history, and enabled state live only under the current user's directory::

    <host_root>/public/<name>/SKILL.md
    <user_custom_root>/<name>/SKILL.md
    <user_custom_root>/.history/<name>.jsonl
    <user_skills_root>/_skill_states.json
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from deerflow.constants import DEFAULT_SKILLS_CONTAINER_PATH
from deerflow.skills.permissions import make_skill_written_path_sandbox_readable
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.storage.skill_storage import SKILL_MD_FILE
from deerflow.skills.types import SkillCategory

logger = logging.getLogger(__name__)


class UserScopedSkillStorage(LocalSkillStorage):
    """Read global public skills and current-user custom skills only."""

    def __init__(
        self,
        user_id: str,
        host_path: str | None = None,
        container_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
        app_config=None,
    ) -> None:
        super().__init__(host_path=host_path, container_path=container_path, app_config=app_config)

        from deerflow.config.paths import _validate_user_id, get_paths

        self._user_id = _validate_user_id(user_id)
        paths = get_paths()
        self._user_custom_root: Path = paths.user_custom_skills_dir(self._user_id)
        self._user_skills_root: Path = paths.user_skills_dir(self._user_id)
        self._skill_states_file: Path = self._user_skills_root / "_skill_states.json"

    # ------------------------------------------------------------------
    # Per-user custom-skill enabled state
    # ------------------------------------------------------------------

    def _read_skill_states(self) -> dict[str, dict[str, bool]]:
        """Read per-user skill enabled states from ``_skill_states.json``.

        Returns a dict keyed by skill name, each value being
        ``{"enabled": True/False}``.  Returns an empty dict if the file
        does not exist or is unreadable.
        """
        if not self._skill_states_file.exists():
            return {}
        try:
            with open(self._skill_states_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read skill states file %s", self._skill_states_file)
        return {}

    def _write_skill_states(self, states: dict[str, dict[str, bool]]) -> None:
        """Persist per-user skill enabled states to ``_skill_states.json``.

        Atomic write via a temp file in the same directory followed by
        ``Path.replace`` (POSIX-atomic on the same filesystem). Without this,
        a crash/SIGTERM/disk-full mid-write would leave the file truncated
        or empty; ``_read_skill_states`` would then return ``{}`` and
        ``get_skill_enabled_state`` would silently re-enable every skill
        the user had disabled. Mirrors the pattern used by
        ``LocalSkillStorage.write_custom_skill`` in this same module.
        """
        self._user_skills_root.mkdir(parents=True, exist_ok=True)
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(self._user_skills_root),
            prefix=".skill_states_",
            suffix=".json.tmp",
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(states, f, indent=2)
            tmp_path.replace(self._skill_states_file)
        except Exception:
            # Best-effort cleanup of the temp file on failure.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def get_skill_enabled_state(self, skill_name: str) -> bool:
        """Return the enabled state for a custom skill.

        Default is ``True`` (newly created skills are enabled by default).
        """
        states = self._read_skill_states()
        entry = states.get(skill_name)
        if entry is None:
            return True
        return entry.get("enabled", True)

    def set_skill_enabled_state(self, skill_name: str, enabled: bool) -> None:
        """Set the enabled state for a custom skill and persist."""
        states = self._read_skill_states()
        states[skill_name] = {"enabled": enabled}
        self._write_skill_states(states)

    # ------------------------------------------------------------------
    # Path helpers — redirect custom skill paths to user directory
    # ------------------------------------------------------------------

    def get_custom_skill_dir(self, name: str) -> Path:
        """Per-user custom skill directory: ``<user_custom_root>/<name>/``."""
        normalized_name = self.validate_skill_name(name)
        return self._user_custom_root / normalized_name

    def get_custom_skill_file(self, name: str) -> Path:
        """Per-user custom SKILL.md path."""
        return self.get_custom_skill_dir(name) / SKILL_MD_FILE

    def get_skill_history_file(self, name: str) -> Path:
        """Per-user custom skill history: ``<user_custom_root>/.history/<name>.jsonl``."""
        normalized_name = self.validate_skill_name(name)
        return self._user_custom_root / ".history" / f"{normalized_name}.jsonl"

    # ------------------------------------------------------------------
    # Enabled state: use per-user state for custom skills
    # ------------------------------------------------------------------

    def load_skills(self, *, enabled_only: bool = False) -> list:
        """Load global public skills and current-user custom skills."""
        skills = super().load_skills(enabled_only=False)
        skills = [
            dataclasses.replace(skill, enabled=self.get_skill_enabled_state(skill.name))
            if dataclasses.is_dataclass(skill)
            and not isinstance(skill, type)
            and skill.category == SkillCategory.CUSTOM
            else skill
            for skill in skills
        ]
        if enabled_only:
            skills = [skill for skill in skills if skill.enabled]
        return skills

    # ------------------------------------------------------------------
    # Skill discovery
    # ------------------------------------------------------------------

    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        public_path = self._host_root / SkillCategory.PUBLIC.value
        if public_path.exists() and public_path.is_dir():
            for current_root, dir_names, file_names in os.walk(public_path, followlinks=True):
                dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
                if SKILL_MD_FILE in file_names:
                    yield SkillCategory.PUBLIC, public_path, Path(current_root) / SKILL_MD_FILE

        if self._user_custom_root.exists() and self._user_custom_root.is_dir():
            for current_root, dir_names, file_names in os.walk(self._user_custom_root, followlinks=True):
                dir_names[:] = sorted(name for name in dir_names if not name.startswith(".") and name != ".history")
                if SKILL_MD_FILE in file_names:
                    yield SkillCategory.CUSTOM, self._user_custom_root, Path(current_root) / SKILL_MD_FILE

    # ------------------------------------------------------------------
    # Install — redirect custom_dir to user directory
    # ------------------------------------------------------------------

    async def ainstall_skill_from_archive(self, archive_path: str | Path) -> dict:
        from deerflow.skills.installer import _scan_skill_archive_contents_or_raise

        logger.info("Installing skill from %s for user %s", archive_path, self._user_id)
        path = Path(archive_path)
        custom_dir = self._user_custom_root

        # Ensure user custom directory exists
        custom_dir.mkdir(parents=True, exist_ok=True)

        # The per-file security scan is an async LLM call and must stay on the
        # event loop; every filesystem phase around it runs in a worker thread.
        tmp = await asyncio.to_thread(tempfile.mkdtemp)
        try:
            skill_dir, skill_name, target = await asyncio.to_thread(self._prepare_skill_archive, path, Path(tmp), custom_dir, archive_path)

            await _scan_skill_archive_contents_or_raise(skill_dir, skill_name)

            await asyncio.to_thread(self._commit_skill_install, skill_dir, skill_name, custom_dir, target)
            logger.info("Skill %r installed to %s for user %s", skill_name, target, self._user_id)
        finally:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._cleanup_install_tmp, tmp),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning("Timed out cleaning up skill install temp dir %s", tmp)

        return {
            "success": True,
            "skill_name": skill_name,
            "message": f"Skill '{skill_name}' installed successfully for user '{self._user_id}'",
        }

    # ------------------------------------------------------------------
    # Write — ensure user custom dir exists before writing
    # ------------------------------------------------------------------

    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        # Ensure user custom skills directory exists
        self._user_custom_root.mkdir(parents=True, exist_ok=True)
        target = self.validate_relative_path(relative_path, self.get_custom_skill_dir(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(target)
        make_skill_written_path_sandbox_readable(self.get_custom_skill_dir(name), target)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def user_id(self) -> str:
        """The user ID this storage is scoped to."""
        return self._user_id

    def get_user_custom_root(self) -> Path:
        """Host path to this user's custom skills root directory."""
        return self._user_custom_root

    # ------------------------------------------------------------------
    # Path validation — accept per-user custom root as well as global root
    # ------------------------------------------------------------------

    def validate_skill_file_path(self, skill_file: Path) -> Path:
        """Accept files under *either* the global root or the per-user custom root.

        Custom skills live in ``_user_custom_root`` which is not a sub-path
        of ``_host_root``, so the default implementation's single-root check
        would reject them.  This override allows both roots.
        """
        resolved_file = skill_file.resolve()
        for allowed_root in (self._host_root.resolve(), self._user_custom_root.resolve()):
            try:
                resolved_file.relative_to(allowed_root)
                return resolved_file
            except ValueError:
                continue
        raise ValueError(f"Resolved skill file {resolved_file} must stay within either the global skills root ({self._host_root.resolve()}) or the per-user custom root ({self._user_custom_root.resolve()}).")
