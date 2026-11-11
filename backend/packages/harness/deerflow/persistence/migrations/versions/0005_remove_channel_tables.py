"""Remove legacy channel persistence tables.

Revision ID: 0005_remove_channel_tables
Revises: 0004_run_ownership
Create Date: 2026-07-15

The local single-user runtime no longer exposes IM channel connections. The
upgrade is intentionally destructive and the removed credential/conversation
data cannot be reconstructed by a downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_remove_channel_tables"
down_revision = "0004_run_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "channel_conversations" in existing_tables:
        op.drop_table("channel_conversations")
    if "channel_credentials" in existing_tables:
        op.drop_table("channel_credentials")
    if "channel_oauth_states" in existing_tables:
        op.drop_table("channel_oauth_states")
    if "channel_connections" in existing_tables:
        op.drop_table("channel_connections")


def downgrade() -> None:
    raise RuntimeError("Migration 0005_remove_channel_tables is irreversible")
