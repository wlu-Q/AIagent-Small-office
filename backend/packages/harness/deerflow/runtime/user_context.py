"""Request-scoped user context for user-based authorization.

This module holds the authenticated user in a request/task-local context variable.
Repository APIs accept either ``AUTO`` (resolve the authenticated user) or an
explicit owner string. There is no unscoped ``None`` bypass.

Dependency direction
--------------------
``persistence`` (lower layer) reads from this module; ``gateway.auth``
(higher layer) writes to it. ``CurrentUser`` is defined here as a
:class:`typing.Protocol` so that ``persistence`` never needs to import
the concrete ``User`` class from ``gateway.auth.models``. Any object
with an ``.id: str`` attribute structurally satisfies the protocol.

Asyncio semantics
-----------------
``ContextVar`` is task-local under asyncio, not thread-local. Each
FastAPI request runs in its own task, so the context is naturally
isolated. ``asyncio.create_task`` and ``asyncio.to_thread`` inherit the
parent task's context, which is typically the intended behaviour; if
a background task must *not* see the foreground user, wrap it with
``contextvars.copy_context()`` to get a clean copy.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Final, Protocol, runtime_checkable


@runtime_checkable
class CurrentUser(Protocol):
    """Structural type for the current authenticated user.

    Any object with an ``.id: str`` attribute satisfies this protocol.
    Concrete implementations live in ``app.gateway.auth.models.User``.
    """

    id: str


_current_user: Final[ContextVar[CurrentUser | None]] = ContextVar("deerflow_current_user", default=None)


def set_current_user(user: CurrentUser) -> Token[CurrentUser | None]:
    """Set the current user for this async task.

    Returns a reset token that should be passed to
    :func:`reset_current_user` in a ``finally`` block to restore the
    previous context.
    """
    return _current_user.set(user)


def reset_current_user(token: Token[CurrentUser | None]) -> None:
    """Restore the context to the state captured by ``token``."""
    _current_user.reset(token)


def get_current_user() -> CurrentUser | None:
    """Return the current user, or ``None`` if unset.

    Safe to call in any context. Used by code paths that can proceed
    without a user (e.g. migration scripts, public endpoints).
    """
    return _current_user.get()


def require_current_user() -> CurrentUser:
    """Return the current user, or raise :class:`RuntimeError`.

    Used by repository code that must not be called outside a
    request-authenticated context. The error message is phrased so
    that a caller debugging a stack trace can locate the offending
    code path.
    """
    user = _current_user.get()
    if user is None:
        raise RuntimeError("repository accessed without user context")
    return user


# ---------------------------------------------------------------------------
# Effective user_id helpers (filesystem isolation)
# ---------------------------------------------------------------------------

DEFAULT_USER_ID: Final[str] = "default"


def get_effective_user_id() -> str:
    """Return the authenticated owner or the auth-disabled local owner."""
    user = _current_user.get()
    if user is None:
        return DEFAULT_USER_ID
    return str(user.id)


def resolve_runtime_user_id(runtime: object | None) -> str:
    """Resolve the owner from runtime context, then authenticated local context."""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        ctx_user_id = context.get("user_id")
        if ctx_user_id:
            return str(ctx_user_id)
    return get_effective_user_id()


# ---------------------------------------------------------------------------
# Sentinel-based user_id resolution
# ---------------------------------------------------------------------------
#
# Repository methods accept a ``user_id`` keyword-only argument that
# defaults to ``AUTO``; explicit values override the request context.


class _AutoSentinel:
    """Singleton marker meaning 'resolve user_id from contextvar'."""

    _instance: _AutoSentinel | None = None

    def __new__(cls) -> _AutoSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<AUTO>"


AUTO: Final[_AutoSentinel] = _AutoSentinel()


def resolve_user_id(
    value: str | _AutoSentinel,
    *,
    method_name: str = "repository method",
) -> str:
    """Resolve ``AUTO`` or an explicit owner string; fail closed if unset."""
    if isinstance(value, _AutoSentinel):
        user = _current_user.get()
        if user is None:
            raise RuntimeError(f"{method_name} called with user_id=AUTO but no user context is set; pass an explicit user_id or set the contextvar via auth middleware.")
        # Coerce to ``str`` at the boundary: ``User.id`` is typed as
        # ``UUID`` for the API surface, but the persistence layer
        # stores ``user_id`` as ``String(64)`` and aiosqlite cannot
        # bind a raw UUID object to a VARCHAR column ("type 'UUID' is
        # not supported"). Honour the documented return type here
        # rather than ripple a type change through every caller.
        return str(user.id)
    return value
