"""ISO 8601 timestamp helpers for the Gateway and embedded runtime.

DeerFlow stores and serializes thread/run timestamps as ISO 8601 UTC
strings to match the LangGraph Platform schema (see
``langgraph_sdk.schema.Thread``, where ``created_at`` / ``updated_at``
are ``datetime`` and JSON-encode to ISO 8601). All timestamp generation
should funnel through :func:`now_iso` so the wire format stays
consistent across endpoints, the embedded ``RunManager``, and the
checkpoint metadata written by the Gateway.

"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["coerce_iso", "now_iso"]


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Example: ``"2026-04-27T03:19:46.511479+00:00"``.
    """
    return datetime.now(UTC).isoformat()


def coerce_iso(value: object) -> str:
    """Normalize current stored timestamp values to an ISO 8601 string.

    ISO strings pass through unchanged. ``datetime`` instances are normalized
    to UTC; empty values become ``""``.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)
