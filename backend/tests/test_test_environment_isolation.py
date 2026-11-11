"""Regression tests for backend test filesystem isolation."""

from __future__ import annotations

import os
from pathlib import Path

import deerflow
from deerflow.config.paths import get_paths


def test_backend_suite_imports_workspace_harness() -> None:
    expected_package = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow"

    assert Path(deerflow.__file__).resolve().is_relative_to(expected_package.resolve())


def test_implicit_runtime_paths_do_not_write_to_real_deerflow_home(tmp_path: Path) -> None:
    expected_home = (tmp_path / "deer-flow-home").resolve()
    real_home = Path(__file__).resolve().parents[1] / ".deer-flow"
    test_user = "test-environment-isolation"

    assert Path(os.environ["DEER_FLOW_HOME"]).resolve() == expected_home
    assert get_paths().base_dir == expected_home

    get_paths().ensure_thread_dirs("isolation-smoke", user_id=test_user)

    assert (expected_home / "users" / test_user / "threads" / "isolation-smoke").is_dir()
    assert not (real_home / "users" / test_user).exists()
