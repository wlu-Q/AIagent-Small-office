from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from deerflow.config.paths import Paths


@dataclass(frozen=True, slots=True)
class ProjectKnowledge:
    memory_id: str
    user_id: str
    project_id: str
    category: str
    topic: str
    statement: str
    status: str
    constraints: tuple[str, ...] = ()
    source_workflow_id: str | None = None
    source_run_id: str | None = None
    source_type: str = "unknown"
    supersedes: tuple[str, ...] = ()
    updated_at: int = 0
    source_message_id: str | None = None
    native_task_id: str | None = None
    evidence: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        for name in ("memory_id", "user_id", "project_id", "category", "topic", "statement", "status"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if type(self.updated_at) is not int or self.updated_at < 0:
            raise ValueError("updated_at must be non-negative")
        if self.status not in {'proposed', 'confirmed', 'verified', 'superseded', 'unresolved'}:
            raise ValueError('Invalid knowledge status')
        if self.status in {'confirmed', 'verified'}:
            legacy = bool(self.source_workflow_id) and self.source_type == (
                'user_confirmation' if self.status == 'confirmed' else 'tool_verification')
            sourced_user = (self.status == 'confirmed' and self.source_type == 'user_statement'
                            and isinstance(self.source_message_id, str) and bool(self.source_message_id.strip()))
            sourced_tools = (self.status == 'verified' and self.source_type == 'tool_verification'
                             and isinstance(self.native_task_id, str) and bool(self.native_task_id.strip())
                             and any(isinstance(item, dict) and item.get('toolCallId') and item.get('tool')
                                     and isinstance(item.get('summary'), str) for item in self.evidence))
            if (not isinstance(self.source_run_id, str) or not self.source_run_id.strip()
                    or not (legacy or sourced_user or sourced_tools)):
                raise ValueError('Effective knowledge requires confirmation/verification provenance')
        if any(not isinstance(item, str) or not item.strip() for item in self.constraints):
            raise ValueError('Invalid knowledge constraint')
        if len(self.statement) > 40000 or len(self.constraints) > 100 or any(len(item) > 4000 for item in self.constraints):
            raise ValueError('Knowledge fact exceeds bounded storage limits')

    def to_record(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "category": self.category,
            "topic": self.topic,
            "statement": self.statement,
            "status": self.status,
            "constraints": list(self.constraints),
            "source_workflow_id": self.source_workflow_id,
            "source_run_id": self.source_run_id,
            "source_type": self.source_type,
            "supersedes": list(self.supersedes),
            "updated_at": self.updated_at,
            "source_message_id": self.source_message_id,
            "native_task_id": self.native_task_id,
            "evidence": list(self.evidence),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_knowledge (
  memory_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  category TEXT NOT NULL,
  topic TEXT NOT NULL,
  statement TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS project_knowledge_topic_idx ON project_knowledge(user_id, project_id, topic, updated_at);
CREATE TABLE IF NOT EXISTS knowledge_digests (
  memory_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_json TEXT NOT NULL, excerpts_json TEXT NOT NULL
);
"""


def _from_payload(payload: dict[str, Any]) -> ProjectKnowledge:
    return ProjectKnowledge(
        memory_id=str(payload["memory_id"]), user_id=str(payload["user_id"]), project_id=str(payload["project_id"]),
        category=str(payload["category"]), topic=str(payload["topic"]), statement=str(payload["statement"]),
        status=str(payload["status"]), constraints=tuple(payload.get("constraints", ())),
        source_workflow_id=payload.get("source_workflow_id"), source_run_id=payload.get("source_run_id"),
        source_type=str(payload.get("source_type", "unknown")), supersedes=tuple(payload.get("supersedes", ())),
        updated_at=int(payload.get("updated_at", 0)),
        source_message_id=payload.get('source_message_id'), native_task_id=payload.get('native_task_id'),
        evidence=tuple(payload.get('evidence', ())),
    )


def _source_identity(item: ProjectKnowledge) -> tuple[Any, ...]:
    return (
        item.user_id,
        item.project_id,
        item.category,
        item.topic,
        item.statement,
        item.constraints,
        item.source_workflow_id,
        item.source_run_id,
        item.source_type,
        item.updated_at,
        item.source_message_id,
        item.native_task_id,
        item.evidence,
    )


def _validate_excerpts(statement: str, values: list[str]) -> None:
    """Require complete source sentences in order and preserve safety qualifiers."""
    units = re.split(r'(?<=[。！？.!?])\s+|\n+', statement)
    bounds = set()
    cursor = 0
    for unit in units:
        if not unit.strip():
            continue
        start = statement.index(unit, cursor)
        bounds.add((start, start + len(unit)))
        cursor = start + len(unit)
    starts = {start for start, _ in bounds}
    ends = {end for _, end in bounds}
    cursor = 0
    for value in values:
        start = statement.find(value, cursor)
        if not value.strip() or start not in starts or start + len(value) not in ends:
            raise ValueError('Digest must preserve whole source sentences in order')
        cursor = start + len(value)
    protected = re.compile(
        r'\b(?:not|no|never|unless|if|only|except|without|fail\w*|must|shall|should|cannot|can.t)\b'
        r'|不|未|禁|仅|只|除|若|如|失败|必须|需要|应当|前提|条件', re.IGNORECASE,
    )
    if any(protected.search(unit) and not any(unit in value for value in values) for unit in units if unit.strip()):
        raise ValueError('Digest must preserve negative and conditional source sentences')


class ProjectKnowledgeStore:
    def __init__(self, *, paths: Paths, user_id: str, now: Callable[[], int] | None = None) -> None:
        self.user_id = str(user_id or "").strip()
        if not self.user_id:
            raise ValueError("user_id is required")
        self.path = paths.user_dir(self.user_id) / "office-project-knowledge.v1.sqlite3"
        self._now = now or (lambda: int(time.time() * 1000))
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
            connection.commit()

    def upsert(self, knowledge: ProjectKnowledge) -> ProjectKnowledge:
        if knowledge.user_id != self.user_id:
            raise ValueError("knowledge user does not match store user")
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT project_id FROM project_knowledge WHERE memory_id=?', (knowledge.memory_id,)).fetchone()
            if row and row['project_id'] != knowledge.project_id:
                raise ValueError('Knowledge identity cannot change project')
            connection.execute(
                "INSERT OR REPLACE INTO project_knowledge(memory_id,user_id,project_id,category,topic,statement,status,payload_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (knowledge.memory_id, knowledge.user_id, knowledge.project_id, knowledge.category, knowledge.topic, knowledge.statement, knowledge.status, json.dumps(knowledge.to_record(), ensure_ascii=False), knowledge.updated_at),
            )
            connection.commit()
        return knowledge

    def create(
        self,
        *,
        project_id: str,
        category: str,
        topic: str,
        statement: str,
        status: str = "proposed",
        source_workflow_id: str | None = None,
        source_run_id: str | None = None,
        source_type: str = "unknown",
        constraints: tuple[str, ...] = (),
    ) -> ProjectKnowledge:
        knowledge = ProjectKnowledge(
            memory_id=f"memory-{uuid.uuid4().hex}", user_id=self.user_id, project_id=str(project_id).strip(),
            category=str(category).strip(), topic=str(topic).strip(), statement=str(statement).strip(), status=str(status).strip(),
            constraints=tuple(constraints), source_workflow_id=source_workflow_id, source_run_id=source_run_id,
            source_type=source_type, updated_at=int(self._now()),
        )
        return self.upsert(knowledge)

    def list(self, *, project_id: str, topic: str | None = None, statuses: tuple[str, ...] | None = None) -> tuple[ProjectKnowledge, ...]:
        query = "SELECT payload_json FROM project_knowledge WHERE user_id = ? AND project_id = ?"
        params: list[Any] = [self.user_id, str(project_id).strip()]
        if topic is not None:
            query += " AND topic = ?"
            params.append(str(topic).strip())
        if statuses:
            query += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_from_payload(json.loads(row["payload_json"])) for row in rows)

    def supersede_topic(self, *, project_id: str, topic: str, replacement: ProjectKnowledge) -> ProjectKnowledge:
        if (replacement.user_id, replacement.project_id, replacement.topic) != (self.user_id, project_id, topic):
            raise ValueError('Replacement scope mismatch')
        if replacement.status not in {'confirmed', 'verified'}:
            raise ValueError('Only confirmed or verified facts can replace a topic')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            identity = connection.execute(
                'SELECT payload_json FROM project_knowledge WHERE memory_id=?',
                (replacement.memory_id,),
            ).fetchone()
            if identity:
                existing = _from_payload(json.loads(identity['payload_json']))
                if _source_identity(existing) != _source_identity(replacement):
                    raise ValueError('Knowledge source identity reused')
                connection.commit()
                return existing
            rows = connection.execute("SELECT payload_json FROM project_knowledge WHERE user_id=? AND project_id=? AND topic=? AND status IN ('confirmed','verified','unresolved') AND memory_id<>?",
                (self.user_id, project_id, topic, replacement.memory_id)).fetchall()
            current = tuple(_from_payload(json.loads(row['payload_json'])) for row in rows)
            def rank(item):
                return (2 if item.source_type in {'user_confirmation', 'user_statement'} else 1, item.updated_at)

            newer = [item for item in current if rank(item) > rank(replacement)]
            tied = [item for item in current if rank(item) == rank(replacement) and (item.statement, item.constraints) != (replacement.statement, replacement.constraints)]
            if newer:
                replacement = replace(replacement, status='superseded')
                current = ()
            elif tied:
                replacement = replace(replacement, status='unresolved')
                current = tuple(tied)
            else:
                replacement = replace(replacement, supersedes=tuple(item.memory_id for item in current))
            for item in current:
                updated = replace(item, status='unresolved' if tied else 'superseded')
                connection.execute(
                    "UPDATE project_knowledge SET status = ?, payload_json = ?, updated_at = ? WHERE memory_id = ? AND user_id = ?",
                    (
                        updated.status,
                        json.dumps(updated.to_record(), ensure_ascii=False),
                        updated.updated_at,
                        item.memory_id,
                        self.user_id,
                    ),
                )
            connection.execute(
                "INSERT OR REPLACE INTO project_knowledge(memory_id,user_id,project_id,category,topic,statement,status,payload_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    replacement.memory_id,
                    replacement.user_id,
                    replacement.project_id,
                    replacement.category,
                    replacement.topic,
                    replacement.statement,
                    replacement.status,
                    json.dumps(replacement.to_record(), ensure_ascii=False),
                    replacement.updated_at,
                ),
            )
            connection.commit()
        return replacement

    def record_once(self, knowledge: ProjectKnowledge) -> ProjectKnowledge:
        """Replayable source IDs must never revive old superseded facts."""
        return self.supersede_topic(project_id=knowledge.project_id, topic=knowledge.topic, replacement=knowledge)

    def digests(self, project_id: str) -> dict[str, tuple[str, list[str]]]:
        with self._connect() as connection:
            return {row['memory_id']: (row['source_json'], json.loads(row['excerpts_json'])) for row in connection.execute(
                'SELECT * FROM knowledge_digests WHERE project_id=?', (project_id,))}

    def save_digests(self, project_id: str, sources: tuple[ProjectKnowledge, ...], excerpts: dict[str, list[str]]) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for source in sources:
                if source.user_id != self.user_id or source.project_id != project_id:
                    raise ValueError('Digest scope mismatch')
                source_json = json.dumps(source.to_record(), ensure_ascii=False)
                row = connection.execute('SELECT payload_json FROM project_knowledge WHERE memory_id=? AND user_id=? AND project_id=?',
                    (source.memory_id, self.user_id, project_id)).fetchone()
                if row is None or json.loads(row['payload_json']) != source.to_record():
                    raise ValueError('Knowledge changed during compaction')
                values = excerpts[source.memory_id]
                if not values or any(not value.strip() or value not in source.statement for value in values):
                    raise ValueError('Digest must quote the source; invented facts are forbidden')
                _validate_excerpts(source.statement, values)
                connection.execute('INSERT OR REPLACE INTO knowledge_digests VALUES(?,?,?,?)',
                    (source.memory_id, project_id, source_json, json.dumps(values, ensure_ascii=False)))
            connection.commit()
