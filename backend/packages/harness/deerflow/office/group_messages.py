# 本文件属于 Office 公共群聊事实模块：只保存用户可见群聊原文、稳定序号和 PM 群聊读取游标。
# 不负责保存 DeerFlow Thread 上下文、Run/Event、工具过程，也不触发或调度 Agent。
# 关联：messages.py 决定群聊路由语义，message_pipeline.py 负责正式保存与投递执行。
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, cast

from deerflow.config.paths import Paths

SenderKind = Literal["user", "employee", "pm"]

_PM_CURSOR_KEY = "pm_last_group_sequence"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE CHECK (length(trim(message_id)) > 0),
    sender_kind TEXT NOT NULL CHECK (sender_kind IN ('user', 'employee', 'pm')),
    sender_id TEXT NOT NULL CHECK (length(trim(sender_id)) > 0),
    sender_display_name TEXT NOT NULL CHECK (length(trim(sender_display_name)) > 0),
    sender_avatar TEXT NOT NULL,
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    mention_employee_ids_json TEXT NOT NULL,
    source_run_id TEXT,
    created_at INTEGER NOT NULL,
    project_id TEXT
);

CREATE TABLE IF NOT EXISTS group_state (
    state_key TEXT PRIMARY KEY,
    integer_value INTEGER NOT NULL CHECK (integer_value >= 0)
);
"""


@dataclass(frozen=True)
class GroupMessage:
    message_id: str
    sequence: int
    sender_kind: SenderKind
    sender_id: str
    sender_display_name: str
    sender_avatar: str
    content: str
    mention_employee_ids: tuple[str, ...]
    source_run_id: str | None
    created_at: int
    project_id: str | None = None


@dataclass(frozen=True)
class GroupMessageDelta:
    messages: tuple[GroupMessage, ...]
    sequence_end: int


class GroupMessageStore:
    """Store the user-visible public group chat in one user-scoped SQLite database."""

    def __init__(self, *, paths: Paths, user_id: str) -> None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("user_id is required")
        self.user_id = normalized_user_id
        self.path = paths.user_dir(normalized_user_id) / "office-group.v1.sqlite3"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            # Extend the existing database in place; never discard chat history.
            # Old rows have no historical display facts, so freeze their stable ID.
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(group_messages)")}
                if "sender_display_name" not in columns:
                    connection.execute("ALTER TABLE group_messages ADD COLUMN sender_display_name TEXT NOT NULL DEFAULT ''")
                    connection.execute("UPDATE group_messages SET sender_display_name = CASE WHEN sender_kind = 'user' THEN '你' ELSE sender_id END")
                if "sender_avatar" not in columns:
                    connection.execute("ALTER TABLE group_messages ADD COLUMN sender_avatar TEXT NOT NULL DEFAULT ''")
                if "project_id" not in columns:
                    connection.execute("ALTER TABLE group_messages ADD COLUMN project_id TEXT")

    @staticmethod
    def _normalize_text(value: object, *, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required")
        return value

    @classmethod
    def _normalize_mentions(cls, values: object) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)):
            raise ValueError("mentionEmployeeIds is invalid")
        mentions = tuple(values)
        if any(not isinstance(value, str) or not value.strip() for value in mentions):
            raise ValueError("mentionEmployeeIds is invalid")
        return cast(tuple[str, ...], mentions)

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> GroupMessage:
        try:
            mentions = cls._normalize_mentions(json.loads(row["mention_employee_ids_json"]))
            message_id = cls._normalize_text(row["message_id"], field_name="messageId")
            sender_kind = row["sender_kind"]
            if sender_kind not in {"user", "employee", "pm"}:
                raise ValueError("senderKind is invalid")
            sender_id = cls._normalize_text(row["sender_id"], field_name="senderId")
            sender_display_name = cls._normalize_text(row["sender_display_name"], field_name="senderDisplayName")
            sender_avatar = row["sender_avatar"]
            if not isinstance(sender_avatar, str):
                raise ValueError("senderAvatar is invalid")
            content = cls._normalize_text(row["content"], field_name="content")
            source_run_id = row["source_run_id"]
            if source_run_id is not None:
                source_run_id = cls._normalize_text(source_run_id, field_name="sourceRunId")
            project_id = row["project_id"]
            if project_id is not None:
                project_id = cls._normalize_text(project_id, field_name="projectId")
            sequence = row["sequence"]
            created_at = row["created_at"]
            if not isinstance(sequence, int) or sequence < 1:
                raise ValueError("sequence must be positive")
            if not isinstance(created_at, int):
                raise ValueError("createdAt is invalid")
            return GroupMessage(
                message_id=message_id,
                sequence=sequence,
                sender_kind=cast(SenderKind, sender_kind),
                sender_id=sender_id,
                sender_display_name=sender_display_name,
                sender_avatar=sender_avatar,
                content=content,
                mention_employee_ids=mentions,
                source_run_id=source_run_id,
                created_at=created_at,
                project_id=project_id,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError("Corrupt Office group message") from error

    @staticmethod
    def _same_fact(
        existing: GroupMessage,
        *,
        sender_kind: SenderKind,
        sender_id: str,
        content: str,
        mention_employee_ids: tuple[str, ...],
        source_run_id: str | None,
        project_id: str | None,
    ) -> bool:
        return (
            existing.sender_kind == sender_kind
            and existing.sender_id == sender_id
            and existing.content == content
            and existing.mention_employee_ids == mention_employee_ids
            and existing.source_run_id == source_run_id
            # A pre-migration row has no project binding. Preserve its old
            # idempotent retry behavior, while never treating a known binding
            # from another project as the same message.
            and (existing.project_id == project_id or existing.project_id is None)
        )

    def append(
        self,
        *,
        message_id: str,
        sender_kind: SenderKind,
        sender_id: str,
        content: str,
        sender_display_name: str | None = None,
        sender_avatar: str = "",
        mention_employee_ids: tuple[str, ...] = (),
        source_run_id: str | None = None,
        project_id: str | None = None,
        created_at: int | None = None,
    ) -> GroupMessage:
        normalized_message_id = self._normalize_text(message_id, field_name="messageId")
        if sender_kind not in {"user", "employee", "pm"}:
            raise ValueError("senderKind is invalid")
        normalized_sender_id = self._normalize_text(sender_id, field_name="senderId")
        normalized_display_name = self._normalize_text(
            sender_display_name if sender_display_name is not None else ("你" if sender_kind == "user" else None),
            field_name="senderDisplayName",
        )
        if not isinstance(sender_avatar, str):
            raise ValueError("senderAvatar is invalid")
        normalized_content = self._normalize_text(content, field_name="content")
        normalized_mentions = self._normalize_mentions(mention_employee_ids)
        normalized_source_run_id = None
        if source_run_id is not None:
            normalized_source_run_id = self._normalize_text(
                source_run_id,
                field_name="sourceRunId",
            )
        normalized_project_id = None
        if project_id is not None:
            normalized_project_id = self._normalize_text(project_id, field_name="projectId")
        normalized_created_at = int(time.time() * 1000) if created_at is None else created_at
        if not isinstance(normalized_created_at, int):
            raise ValueError("createdAt is invalid")

        with self._connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                "SELECT * FROM group_messages WHERE message_id = ?",
                (normalized_message_id,),
            ).fetchone()
            if existing_row is not None:
                existing = self._from_row(existing_row)
                if self._same_fact(
                    existing,
                    sender_kind=sender_kind,
                    sender_id=normalized_sender_id,
                    content=normalized_content,
                    mention_employee_ids=normalized_mentions,
                    source_run_id=normalized_source_run_id,
                    project_id=normalized_project_id,
                ):
                    return existing
                raise ValueError(
                    f"Group message {normalized_message_id} already exists with different content"
                )

            cursor = connection.execute(
                """
                INSERT INTO group_messages (
                    message_id,
                    sender_kind,
                    sender_id,
                    sender_display_name,
                    sender_avatar,
                    content,
                    mention_employee_ids_json,
                    source_run_id,
                    created_at,
                    project_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_message_id,
                    sender_kind,
                    normalized_sender_id,
                    normalized_display_name,
                    sender_avatar,
                    normalized_content,
                    json.dumps(
                        list(normalized_mentions),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    normalized_source_run_id,
                    normalized_created_at,
                    normalized_project_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM group_messages WHERE sequence = ?",
                (cursor.lastrowid,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Inserted Office group message was not found")
            return self._from_row(row)

    def list(self, *, after_sequence: int = 0, limit: int = 100) -> list[GroupMessage]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM group_messages
                WHERE sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (after_sequence, limit),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def find(self, message_id: str) -> GroupMessage | None:
        normalized = self._normalize_text(message_id, field_name="message_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM group_messages WHERE message_id = ?",
                (normalized,),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def clear(self) -> int:
        """Delete every group message and reset the PM read cursor to zero.

        The cursor has to move with the messages: leaving it ahead of an emptied table
        would make the next ``read_pm_delta`` skip whatever is written afterwards.
        Returns the number of messages deleted.
        """
        with self._connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute("DELETE FROM group_messages").rowcount
            connection.execute(
                """
                INSERT INTO group_state (state_key, integer_value)
                VALUES (?, 0)
                ON CONFLICT(state_key) DO UPDATE SET integer_value = 0
                """,
                (_PM_CURSOR_KEY,),
            )
            # AUTOINCREMENT keeps its high-water mark in sqlite_sequence. Left alone the
            # first message after a reset resumes at a stale sequence, and the frontend
            # (which tracks nextAfterSequence) would never see it.
            has_sequence_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
            ).fetchone()
            if has_sequence_table is not None:
                connection.execute("DELETE FROM sqlite_sequence WHERE name = 'group_messages'")
        return int(deleted or 0)

    def get_pm_cursor(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT integer_value FROM group_state WHERE state_key = ?",
                (_PM_CURSOR_KEY,),
            ).fetchone()
        if row is None:
            return 0
        sequence = row["integer_value"]
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError("Corrupt Office group state")
        return sequence

    def read_pm_delta(self) -> GroupMessageDelta:
        """Read one stable public-chat slice after the confirmed PM cursor."""
        with self._connect() as connection, connection:
            connection.execute("BEGIN")
            state = connection.execute(
                "SELECT integer_value FROM group_state WHERE state_key = ?",
                (_PM_CURSOR_KEY,),
            ).fetchone()
            cursor = 0 if state is None else state["integer_value"]
            if not isinstance(cursor, int) or cursor < 0:
                raise ValueError("Corrupt Office group state")
            rows = connection.execute(
                """
                SELECT *
                FROM group_messages
                WHERE sequence > ?
                ORDER BY sequence
                """,
                (cursor,),
            ).fetchall()
        messages = tuple(self._from_row(row) for row in rows)
        sequence_end = messages[-1].sequence if messages else cursor
        return GroupMessageDelta(messages=messages, sequence_end=sequence_end)

    def advance_pm_cursor(self, sequence: int) -> int:
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must not be negative")
        with self._connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT integer_value FROM group_state WHERE state_key = ?",
                (_PM_CURSOR_KEY,),
            ).fetchone()
            current = 0 if row is None else row["integer_value"]
            if not isinstance(current, int) or current < 0:
                raise ValueError("Corrupt Office group state")
            if sequence < current:
                raise ValueError("PM group cursor must not move backwards")
            if sequence == current:
                return current
            connection.execute(
                """
                INSERT INTO group_state (state_key, integer_value)
                VALUES (?, ?)
                ON CONFLICT(state_key) DO UPDATE SET integer_value = excluded.integer_value
                """,
                (_PM_CURSOR_KEY, sequence),
            )
            return sequence
