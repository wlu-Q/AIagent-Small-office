"""Test configuration for the backend test suite.

Sets up sys.path and pre-mocks modules that would cause circular import
issues when unit-testing lightweight config/registry code in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Make the workspace copies of 'deerflow' and 'app' win over stale wheels.
_BACKEND_ROOT = Path(__file__).parent.parent
_HARNESS_ROOT = _BACKEND_ROOT / "packages" / "harness"
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_HARNESS_ROOT))

@pytest.fixture(autouse=True)
def _require_auth_unless_test_overrides(monkeypatch):
    """Keep legacy auth tests explicit while production defaults to local mode."""
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "0")


@pytest.fixture(autouse=True)
def _isolate_deer_flow_home(tmp_path, monkeypatch):
    """Keep implicit runtime writes inside the current test sandbox."""
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "deer-flow-home"))
    monkeypatch.setenv(
        "DEER_FLOW_CONFIG_PATH",
        str(_BACKEND_ROOT.parent / "config.yaml"),
    )
    monkeypatch.delenv("DEER_FLOW_HOST_BASE_DIR", raising=False)


# ---------------------------------------------------------------------------
# Auto-set user context for every test unless marked no_auto_user
# ---------------------------------------------------------------------------
#
# Repository methods read ``user_id`` from a contextvar by default
# (see ``deerflow.runtime.user_context``). Without this fixture, every
# pre-existing persistence test would raise RuntimeError because the
# contextvar is unset. The fixture sets a default test user on every
# test; tests that explicitly want to verify behaviour *without* a user
# context should mark themselves ``@pytest.mark.no_auto_user``.


@pytest.fixture(autouse=True)
def _reset_skill_storage_singleton():
    """Reset the SkillStorage singleton between tests to prevent cross-test contamination."""
    try:
        from deerflow.skills.storage import reset_skill_storage
    except ImportError:
        yield
        return
    reset_skill_storage()
    try:
        yield
    finally:
        reset_skill_storage()


@pytest.fixture(autouse=True)
def _restore_title_config_singleton():
    """Reset ``_title_config`` to its pristine default after every test.

    ``AppConfig.from_file()`` writes the on-disk ``title`` block into the
    module-level singleton (``config/app_config.py`` calls
    ``load_title_config_from_dict``). Any test that loads the real
    ``config.yaml`` therefore leaves the singleton in a state that
    ``test_title_middleware_core_logic.py`` does not expect; that suite
    relies on the pristine ``TitleConfig()`` default (``enabled=True``).
    We restore the default after every test so test files stay
    independent regardless of order.
    """
    try:
        from deerflow.config.title_config import reset_title_config
    except ImportError:
        yield
        return

    try:
        yield
    finally:
        reset_title_config()


@pytest.fixture(autouse=True)
def _auto_user_context(request):
    """Inject a default ``test-user-autouse`` into the contextvar.

    Opt-out via ``@pytest.mark.no_auto_user``. Uses lazy import so that
    tests which don't touch the persistence layer never pay the cost
    of importing runtime.user_context.
    """
    if request.node.get_closest_marker("no_auto_user"):
        yield
        return

    try:
        from deerflow.runtime.user_context import (
            reset_current_user,
            set_current_user,
        )
    except ImportError:
        yield
        return

    user = SimpleNamespace(id="test-user-autouse", email="test@local")
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)
