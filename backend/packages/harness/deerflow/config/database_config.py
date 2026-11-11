"""Unified database backend configuration.

Controls BOTH the LangGraph checkpointer and the DeerFlow application
persistence layer (runs, threads metadata, users, etc.). The user
configures one backend; the system handles physical separation details.

SQLite mode: checkpointer and app share a single .db file
({sqlite_dir}/deerflow.db) with WAL journal mode enabled on every
connection. WAL allows concurrent readers and a single writer without
blocking, making a unified file safe for both workloads.  Writers
that contend for the lock wait via the default 5-second sqlite3
busy timeout rather than failing immediately.

Postgres mode: both use the same database URL but maintain independent
connection pools with different lifecycles.

Memory mode: checkpointer uses MemorySaver, app uses in-memory stores.
No database is initialized.

Sensitive values (postgres_url) should use $VAR syntax in config.yaml
to reference environment variables from .env:

    database:
      backend: postgres
      postgres_url: $DATABASE_URL

The $VAR resolution is handled by AppConfig.resolve_env_variables()
before this config is instantiated -- DatabaseConfig itself does not
need to do any environment variable processing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    backend: Literal["memory", "sqlite", "postgres"] = Field(
        default="memory",
        description=("Storage backend for both checkpointer and application data. 'memory' for development (no persistence across restarts), 'sqlite' for single-node deployment, 'postgres' for production multi-node deployment."),
    )
    sqlite_dir: str = Field(
        default="data",
        description=("Directory under DEER_FLOW_HOME for the shared SQLite database, or an explicit absolute path."),
    )
    postgres_url: str = Field(
        default="",
        description=(
            "PostgreSQL connection URL, shared by checkpointer and app. "
            "Use $DATABASE_URL in config.yaml to reference .env. "
            "Example: postgresql://user:pass@host:5432/deerflow "
            "(the +asyncpg driver suffix is added automatically where needed)."
        ),
    )
    echo_sql: bool = Field(
        default=False,
        description="Echo all SQL statements to log (debug only).",
    )
    pool_size: int = Field(
        default=5,
        description="Connection pool size for the app ORM engine (postgres only).",
    )

    # -- Derived helpers (not user-configured) --

    @property
    def sqlite_directory(self) -> str:
        """Resolve SQLite storage from the single DEER_FLOW_HOME data root."""
        configured = Path(self.sqlite_dir).expanduser()
        if configured.is_absolute():
            return str(configured.resolve())
        from deerflow.config.paths import get_paths

        return str((get_paths().base_dir / configured).resolve())

    @property
    def sqlite_path(self) -> str:
        """Unified SQLite file path shared by checkpointer and app."""
        return str(Path(self.sqlite_directory) / "deerflow.db")

    @property
    def app_sqlalchemy_url(self) -> str:
        """SQLAlchemy async URL for the application ORM engine."""
        if self.backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        if self.backend == "postgres":
            url = self.postgres_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        raise ValueError(f"No SQLAlchemy URL for backend={self.backend!r}")
