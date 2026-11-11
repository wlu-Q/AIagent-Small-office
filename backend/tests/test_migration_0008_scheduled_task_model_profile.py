from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade

pytestmark = pytest.mark.asyncio


async def test_migration_binds_scheduled_tasks_without_hidden_default(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'scheduled.db').as_posix()}")
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "0007_finalize_run_event_boundary")

        async with engine.begin() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in sa.inspect(sync_conn).get_columns("scheduled_tasks")
                }
            )
            assert "model_profile_id" not in columns

            # Simulate a database that already received an explicit binding
            # column through an early build. The migration must preserve those
            # bindings while disabling only unsafe historical tasks.
            await conn.execute(
                sa.text(
                    "ALTER TABLE scheduled_tasks "
                    "ADD COLUMN model_profile_id VARCHAR(128) NULL"
                )
            )
            for task_id, status, last_error, model_profile_id in (
                ("enabled-without-profile", "enabled", None, None),
                ("running-without-profile", "running", None, None),
                ("paused-without-profile", "paused", "keep-existing-error", None),
                ("enabled-with-profile", "enabled", "keep-profile-error", "profile-main"),
            ):
                await conn.execute(
                    sa.text(
                        "INSERT INTO scheduled_tasks ("
                        "id, user_id, thread_id, context_mode, assistant_id, title, prompt, "
                        "schedule_type, schedule_spec, timezone, status, overlap_policy, "
                        "next_run_at, last_run_at, last_run_id, last_thread_id, last_error, "
                        "lease_owner, lease_expires_at, run_count, created_at, updated_at, "
                        "model_profile_id"
                        ") VALUES ("
                        ":id, 'default', NULL, 'fresh_thread_per_run', NULL, :title, 'prompt', "
                        "'interval', :schedule_spec, 'UTC', :status, 'skip', NULL, NULL, NULL, "
                        "NULL, :last_error, NULL, NULL, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                        ":model_profile_id"
                        ")"
                    ),
                    {
                        "id": task_id,
                        "title": task_id,
                        "schedule_spec": '{"seconds": 60}',
                        "status": status,
                        "last_error": last_error,
                        "model_profile_id": model_profile_id,
                    },
                )

        await asyncio.to_thread(_upgrade, cfg, "head")

        async with engine.connect() as conn:
            columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in sa.inspect(sync_conn).get_columns("scheduled_tasks")
                }
            )
            rows = (
                await conn.execute(
                    sa.text(
                        "SELECT id, status, last_error, model_profile_id "
                        "FROM scheduled_tasks ORDER BY id"
                    )
                )
            ).mappings()
            tasks = {row["id"]: dict(row) for row in rows}
            revision = await conn.scalar(sa.text("SELECT version_num FROM alembic_version"))

        assert "model_profile_id" in columns
        assert revision == '0008_scheduled_task_model_profile'
        assert tasks["enabled-without-profile"]["status"] == "paused"
        assert tasks["running-without-profile"]["status"] == "paused"
        assert tasks["enabled-without-profile"]["last_error"] == (
            "Model profile selection required after migration"
        )
        assert tasks["running-without-profile"]["last_error"] == (
            "Model profile selection required after migration"
        )
        assert tasks["paused-without-profile"]["status"] == "paused"
        assert tasks["paused-without-profile"]["last_error"] == "keep-existing-error"
        assert tasks["enabled-with-profile"]["status"] == "enabled"
        assert tasks["enabled-with-profile"]["last_error"] == "keep-profile-error"
        assert tasks["enabled-with-profile"]["model_profile_id"] == "profile-main"
    finally:
        await engine.dispose()
