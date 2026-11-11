"""SQLAlchemy-backed thread metadata repository."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.json_compat import json_match
from deerflow.persistence.thread_meta.base import InvalidMetadataFilterError, ThreadMetaStore
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso

logger = logging.getLogger(__name__)


class ThreadMetaRepository(ThreadMetaStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _row_to_dict(row: ThreadMetaRow) -> dict[str, Any]:
        d = row.to_dict()
        d["metadata"] = d.pop("metadata_json", None) or {}
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                # SQLite drops tzinfo despite ``DateTime(timezone=True)``;
                # ``coerce_iso`` normalizes naive values as UTC so the wire format always carries tz.
                d[key] = coerce_iso(val)
        return d

    async def create(
        self,
        thread_id: str,
        *,
        assistant_id: str | None = None,
        user_id: str | _AutoSentinel = AUTO,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.create")
        now = datetime.now(UTC)
        row = ThreadMetaRow(
            thread_id=thread_id,
            assistant_id=assistant_id,
            user_id=resolved_user_id,
            display_name=display_name,
            metadata_json=metadata or {},
            created_at=now,
            updated_at=now,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._row_to_dict(row)

    async def get(
        self,
        thread_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> dict | None:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.get")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return None
            if row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def check_access(self, thread_id: str, user_id: str) -> bool:
        """Return whether an existing thread metadata row belongs to ``user_id``."""
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            return row is not None and row.user_id == user_id

    async def search(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | _AutoSentinel = AUTO,
    ) -> list[dict[str, Any]]:
        """Search threads with optional metadata and status filters.

        The owner filter is always enforced.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.search")
        stmt = select(ThreadMetaRow).order_by(ThreadMetaRow.updated_at.desc(), ThreadMetaRow.thread_id.desc())
        stmt = stmt.where(ThreadMetaRow.user_id == resolved_user_id)
        if status:
            stmt = stmt.where(ThreadMetaRow.status == status)

        if metadata:
            applied = 0
            for key, value in metadata.items():
                try:
                    stmt = stmt.where(json_match(ThreadMetaRow.metadata_json, key, value))
                    applied += 1
                except (ValueError, TypeError) as exc:
                    logger.warning("Skipping metadata filter key %s: %s", ascii(key), exc)
            if applied == 0:
                # Comma-separated plain string (no list repr / nested
                # quoting) so the 400 detail surfaced by the Gateway is
                # easy for clients to read. Sorted for determinism.
                rejected_keys = ", ".join(sorted(str(k) for k in metadata))
                raise InvalidMetadataFilterError(f"All metadata filter keys were rejected as unsafe: {rejected_keys}")

        stmt = stmt.limit(limit).offset(offset)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def _check_ownership(self, session: AsyncSession, thread_id: str, resolved_user_id: str) -> bool:
        """Return whether the row exists and belongs to the user."""
        row = await session.get(ThreadMetaRow, thread_id)
        return row is not None and row.user_id == resolved_user_id

    async def update_display_name(
        self,
        thread_id: str,
        display_name: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> None:
        """Update the display_name (title) for a thread."""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_display_name")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(display_name=display_name, updated_at=datetime.now(UTC)))
            await session.commit()

    async def update_status(
        self,
        thread_id: str,
        status: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> None:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_status")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(status=status, updated_at=datetime.now(UTC)))
            await session.commit()

    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> None:
        """Merge ``metadata`` into ``metadata_json``.

        Read-modify-write inside a single session/transaction so concurrent
        callers see consistent state. No-op if the row does not exist or
        the user_id check fails.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_metadata")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return
            if row.user_id != resolved_user_id:
                return
            merged = dict(row.metadata_json or {})
            merged.update(metadata)
            row.metadata_json = merged
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def update_owner(
        self,
        thread_id: str,
        owner_user_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> None:
        """Move a thread metadata row to ``owner_user_id``."""
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.update_owner")
        async with self._sf() as session:
            if not await self._check_ownership(session, thread_id, resolved_user_id):
                return
            await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == thread_id).values(user_id=owner_user_id, updated_at=datetime.now(UTC)))
            await session.commit()

    async def delete(
        self,
        thread_id: str,
        *,
        user_id: str | _AutoSentinel = AUTO,
    ) -> None:
        resolved_user_id = resolve_user_id(user_id, method_name="ThreadMetaRepository.delete")
        async with self._sf() as session:
            row = await session.get(ThreadMetaRow, thread_id)
            if row is None:
                return
            if row.user_id != resolved_user_id:
                return
            await session.delete(row)
            await session.commit()
