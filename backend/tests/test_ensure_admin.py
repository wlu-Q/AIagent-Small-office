"""Tests for the administrator bootstrap check."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.auth.config import AuthConfig, set_auth_config

_JWT_SECRET = "test-secret-key-ensure-admin-testing-min-32"
os.environ.setdefault("AUTH_JWT_SECRET", _JWT_SECRET)


@pytest.fixture(autouse=True)
def _setup_auth_config():
    set_auth_config(AuthConfig(jwt_secret=_JWT_SECRET))
    yield
    set_auth_config(AuthConfig(jwt_secret=_JWT_SECRET))


def _app_stub():
    return SimpleNamespace(state=SimpleNamespace())


def test_first_boot_does_not_create_admin():
    provider = AsyncMock()
    provider.count_admin_users = AsyncMock(return_value=0)
    provider.create_user = AsyncMock()

    with patch("app.gateway.deps.get_local_provider", return_value=provider):
        from app.gateway.app import _ensure_admin_user

        asyncio.run(_ensure_admin_user(_app_stub()))

    provider.create_user.assert_not_called()


def test_existing_admin_completes_bootstrap_check():
    provider = AsyncMock()
    provider.count_admin_users = AsyncMock(return_value=1)

    with patch("app.gateway.deps.get_local_provider", return_value=provider):
        from app.gateway.app import _ensure_admin_user

        asyncio.run(_ensure_admin_user(_app_stub()))

    provider.count_admin_users.assert_awaited_once()


def test_unavailable_auth_persistence_does_not_abort_startup():
    with patch("app.gateway.deps.get_local_provider", side_effect=RuntimeError("not ready")):
        from app.gateway.app import _ensure_admin_user

        asyncio.run(_ensure_admin_user(_app_stub()))
