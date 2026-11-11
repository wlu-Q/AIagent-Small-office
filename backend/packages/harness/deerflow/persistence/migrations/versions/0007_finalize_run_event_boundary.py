"""Add the durable run event-finalization boundary.

Revision ID: 0007_finalize_run_event_boundary
Revises: 0006_remove_run_ownership
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_finalize_run_event_boundary"
down_revision = "0006_remove_run_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "runs" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("runs")}
    if "events_finalized" not in existing_columns:
        op.add_column(
            "runs",
            sa.Column("events_finalized", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if "last_event_seq" not in existing_columns:
        op.add_column(
            "runs",
            sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0007_finalize_run_event_boundary is irreversible: the event drain boundary is runtime truth"
    )
