"""Credential detection and redaction for durable runtime boundaries.

Runtime inputs may contain user-selected model credentials while durable run
state must remain credential-free. This module is the single policy point for
detecting credential-bearing fields, collecting run-local secret values, and
redacting every persistence or observability boundary.
"""

from __future__ import annotations

import copy
import re
import traceback
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from typing import Any

from pydantic import BaseModel

REDACTED = "[redacted]"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_IDENTIFIER = re.compile(r"[^a-z0-9]+")
_CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "api_token",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential",
        "credentials",
        "id_token",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "access_token",
        "auth_token",
        "session_token",
        "secret",
        "token",
    }
)
_CREDENTIAL_FIELD_SUFFIXES = (
    "_api_key",
    "_api_token",
    "_authorization",
    "_client_secret",
    "_credential",
    "_credentials",
    "_password",
    "_passwd",
    "_private_key",
    "_refresh_token",
    "_access_token",
    "_auth_token",
    "_bearer_token",
    "_id_token",
    "_session_token",
    "_secret",
)


def normalize_field_name(name: object) -> str:
    """Normalize snake, kebab, dotted, and camel-case field names."""
    text = _CAMEL_BOUNDARY.sub("_", str(name)).lower()
    return _NON_IDENTIFIER.sub("_", text).strip("_")


def is_credential_field(name: object) -> bool:
    """Return whether a field semantically stores authentication material.

    Token accounting fields are deliberately not matched by substring. For
    example, max_tokens, input_token_count, and token_budget are ordinary
    runtime data rather than credentials.
    """
    normalized = normalize_field_name(name)
    return normalized in _CREDENTIAL_FIELD_NAMES or normalized.endswith(
        _CREDENTIAL_FIELD_SUFFIXES
    )


def contains_credential_fields(value: Any) -> bool:
    """Recursively detect credential-bearing field names in persisted data."""
    return _contains_credential_fields(value, seen=set())


def _contains_credential_fields(value: Any, *, seen: set[int]) -> bool:
    if isinstance(value, (str, bytes, bytearray, int, float, bool, type(None))):
        return False
    object_id = id(value)
    if object_id in seen:
        return False
    seen.add(object_id)

    items = _object_items(value)
    if items is not None:
        for key, nested in items:
            if is_credential_field(key) or _contains_credential_fields(
                nested, seen=seen
            ):
                return True
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_credential_fields(item, seen=seen) for item in value)
    return False


def collect_secret_values(*sources: Any) -> frozenset[str]:
    """Extract concrete credential values from run-local configuration."""
    secrets: set[str] = set()
    seen: set[int] = set()
    for source in sources:
        _collect_secret_values(source, secrets=secrets, seen=seen)
    return frozenset(secret for secret in secrets if secret and secret != REDACTED)


def _collect_secret_values(
    value: Any,
    *,
    secrets: set[str],
    seen: set[int],
    credential_context: bool = False,
    credential_tree_context: bool = False,
) -> None:
    revealed = _reveal_secret(value)
    if revealed is not None:
        if credential_context or credential_tree_context:
            secrets.add(revealed)
        return
    if isinstance(value, (bytes, bytearray)):
        if credential_context or credential_tree_context:
            try:
                secrets.add(bytes(value).decode("utf-8"))
            except UnicodeDecodeError:
                pass
        return
    if isinstance(value, (int, float, bool, type(None))):
        return

    object_id = id(value)
    if object_id in seen:
        return
    seen.add(object_id)

    items = _object_items(value)
    if items is not None:
        for key, nested in items:
            normalized_key = normalize_field_name(key)
            _collect_secret_values(
                nested,
                secrets=secrets,
                seen=seen,
                credential_context=is_credential_field(key),
                credential_tree_context=(
                    credential_tree_context
                    or normalized_key in {'credential', 'credentials'}
                ),
            )
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect_secret_values(
                item,
                secrets=secrets,
                seen=seen,
                credential_context=credential_context,
                credential_tree_context=credential_tree_context,
            )


def redact_text(value: str, secret_values: set[str] | frozenset[str]) -> str:
    """Replace every known run-local secret in free-form text."""
    redacted = value
    for secret in sorted(secret_values, key=len, reverse=True):
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    return redacted


def redact_value(
    value: Any,
    secret_values: set[str] | frozenset[str] = frozenset(),
    *,
    _seen: dict[int, Any] | None = None,
) -> Any:
    """Return a recursively redacted copy while preserving message objects."""
    secrets = frozenset(secret_values)
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, bytes):
        try:
            return redact_text(value.decode("utf-8"), secrets).encode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, bytearray):
        return bytearray(redact_value(bytes(value), secrets))
    if isinstance(value, (int, float, bool, type(None))):
        return value

    seen = _seen if _seen is not None else {}
    object_id = id(value)
    if object_id in seen:
        return seen[object_id]

    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        seen[object_id] = result
        for key, nested in value.items():
            result[key] = (
                REDACTED
                if is_credential_field(key)
                else redact_value(nested, secrets, _seen=seen)
            )
        try:
            return type(value)(result)
        except (TypeError, ValueError):
            return result

    if isinstance(value, BaseModel):
        updates: dict[str, Any] = {}
        seen[object_id] = value
        for name in type(value).model_fields:
            nested = getattr(value, name, None)
            updates[name] = (
                REDACTED
                if is_credential_field(name)
                else redact_value(nested, secrets, _seen=seen)
            )
        copied = value.model_copy(update=updates, deep=False)
        seen[object_id] = copied
        return copied

    if is_dataclass(value) and not isinstance(value, type):
        updates = {
            field.name: (
                REDACTED
                if is_credential_field(field.name)
                else redact_value(getattr(value, field.name), secrets, _seen=seen)
            )
            for field in fields(value)
        }
        try:
            copied = replace(value, **updates)
        except (TypeError, ValueError):
            copied = copy.copy(value)
            for name, nested in updates.items():
                try:
                    setattr(copied, name, nested)
                except (AttributeError, TypeError):
                    return value
        seen[object_id] = copied
        return copied

    if isinstance(value, list):
        result_list: list[Any] = []
        seen[object_id] = result_list
        result_list.extend(redact_value(item, secrets, _seen=seen) for item in value)
        return result_list
    if isinstance(value, tuple):
        items = tuple(redact_value(item, secrets, _seen=seen) for item in value)
        if hasattr(value, "_fields"):
            try:
                return type(value)(*items)
            except TypeError:
                return items
        return items
    if isinstance(value, set):
        return {redact_value(item, secrets, _seen=seen) for item in value}
    if isinstance(value, frozenset):
        return frozenset(redact_value(item, secrets, _seen=seen) for item in value)
    return value


def redact_exception_traceback(
    error: BaseException,
    secret_values: set[str] | frozenset[str],
) -> str:
    """Format an exception traceback without re-emitting provider secrets."""
    rendered = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    return redact_text(rendered, frozenset(secret_values)).rstrip()


class RedactingCheckpointer:
    """Proxy that redacts all data before a checkpoint persistence write."""

    def __init__(self, delegate: Any, secret_values: set[str] | frozenset[str]):
        self._delegate = delegate
        self._secret_values = frozenset(secret_values)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def _redact_call(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        return (
            tuple(redact_value(arg, self._secret_values) for arg in args),
            {
                key: redact_value(value, self._secret_values)
                for key, value in kwargs.items()
            },
        )

    def put(self, *args: Any, **kwargs: Any) -> Any:
        safe_args, safe_kwargs = self._redact_call(args, kwargs)
        return self._delegate.put(*safe_args, **safe_kwargs)

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        safe_args, safe_kwargs = self._redact_call(args, kwargs)
        return await self._delegate.aput(*safe_args, **safe_kwargs)

    def put_writes(self, *args: Any, **kwargs: Any) -> Any:
        safe_args, safe_kwargs = self._redact_call(args, kwargs)
        return self._delegate.put_writes(*safe_args, **safe_kwargs)

    async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
        safe_args, safe_kwargs = self._redact_call(args, kwargs)
        return await self._delegate.aput_writes(*safe_args, **safe_kwargs)


def redacting_checkpointer(
    checkpointer: Any,
    secret_values: set[str] | frozenset[str],
) -> Any:
    """Wrap a checkpointer only when a run actually has secrets to protect."""
    if checkpointer is None or not secret_values:
        return checkpointer
    if isinstance(checkpointer, RedactingCheckpointer):
        return checkpointer
    return RedactingCheckpointer(checkpointer, secret_values)


def _reveal_secret(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        revealed = getter()
        return revealed if isinstance(revealed, str) else str(revealed)
    return None


def _object_items(value: Any) -> list[tuple[object, Any]] | None:
    if isinstance(value, Mapping):
        return list(value.items())
    if isinstance(value, BaseModel):
        return [
            (name, getattr(value, name, None))
            for name in type(value).model_fields
        ]
    if is_dataclass(value) and not isinstance(value, type):
        return [(field.name, getattr(value, field.name)) for field in fields(value)]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="python")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return list(dumped.items())
    return None
