"""Bind scheduled tasks to an explicit model profile.

Revision ID: 0008_scheduled_task_model_profile
Revises: 0007_finalize_run_event_boundary
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_scheduled_task_model_profile"
down_revision = "0007_finalize_run_event_boundary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scheduled_tasks" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("scheduled_tasks")}
    if "model_profile_id" not in existing_columns:
        op.add_column(
            "scheduled_tasks",
            sa.Column("model_profile_id", sa.String(length=128), nullable=True),
        )

    # Existing tasks predate model ownership and must never use a hidden default.
    bind.execute(
        sa.text(
            "UPDATE scheduled_tasks "
            "SET status = 'paused', "
            "last_error = 'Model profile selection required after migration' "
            "WHERE model_profile_id IS NULL AND status IN ('enabled', 'running')"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0008_scheduled_task_model_profile is irreversible: scheduled runs require explicit model ownership"
    )
