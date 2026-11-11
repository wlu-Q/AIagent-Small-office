"""Remove multi-worker run ownership schema.

Revision ID: 0006_remove_run_ownership
Revises: 0005_remove_channel_tables
Create Date: 2026-07-15

The local desktop product runs one Gateway process. Cross-worker run leases and
thread-level ownership indexes are therefore outside the supported runtime. The
columns and indexes are removed rather than retained as dormant schema debt.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_remove_run_ownership"
down_revision = "0005_remove_channel_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "runs" not in inspector.get_table_names():
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("runs")}
    if "uq_runs_thread_active" in existing_indexes:
        op.drop_index("uq_runs_thread_active", table_name="runs")
    if "ix_runs_lease" in existing_indexes:
        op.drop_index("ix_runs_lease", table_name="runs")

    existing_columns = {column["name"] for column in inspector.get_columns("runs")}
    if "lease_expires_at" in existing_columns:
        op.drop_column("runs", "lease_expires_at")
    if "owner_worker_id" in existing_columns:
        op.drop_column("runs", "owner_worker_id")


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0006_remove_run_ownership is irreversible: local runs have no cross-worker owner or lease"
    )
