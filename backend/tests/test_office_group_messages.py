# 本文件属于 Office 公共群聊事实测试模块：只验证可见消息、稳定序号、幂等写入和 PM 游标。
# 不负责 DeerFlow Thread 上下文、Agent 执行、消息投递、员工排队或模型回复生成测试。
# 关联：office/group_messages.py 是被测事实存储，message_pipeline.py 决定正式保存时机。
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.config.paths import Paths
from deerflow.office.group_messages import GroupMessageStore


def build_store(tmp_path, user_id: str = "user-a") -> GroupMessageStore:
    return GroupMessageStore(paths=Paths(tmp_path / ".deer-flow"), user_id=user_id)


def test_display_snapshot_survives_reopen_and_retry_does_not_rewrite_it(tmp_path) -> None:
    store = build_store(tmp_path)
    original = store.append(
        message_id="stable-reply", sender_kind="employee", sender_id="employee-a",
        sender_display_name="原来的姓名", sender_avatar="adam", content="完成了", source_run_id="run-one",
    )
    reopened = build_store(tmp_path)
    retried = reopened.append(
        message_id="stable-reply", sender_kind="employee", sender_id="employee-a",
        sender_display_name="新的姓名", sender_avatar="ash", content="完成了", source_run_id="run-one",
    )
    assert retried == original == reopened.list()[0]
    assert retried.sender_display_name == "原来的姓名"
    assert retried.sender_avatar == "adam"


def test_adding_display_columns_preserves_existing_messages_and_cursor(tmp_path) -> None:
    path = Paths(tmp_path / ".deer-flow").user_dir("user-a") / "office-group.v1.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE group_messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
                sender_kind TEXT NOT NULL, sender_id TEXT NOT NULL, content TEXT NOT NULL,
                mention_employee_ids_json TEXT NOT NULL, source_run_id TEXT, created_at INTEGER NOT NULL
            );
            CREATE TABLE group_state (state_key TEXT PRIMARY KEY, integer_value INTEGER NOT NULL);
            INSERT INTO group_messages VALUES (1, 'old', 'employee', 'deleted-id', 'history', '[]', NULL, 1);
            INSERT INTO group_state VALUES ('pm_last_group_sequence', 1);
        ''')
    store = build_store(tmp_path)
    old = store.list()[0]
    # Past display details were never stored: retain the stable ID, do not invent a past name.
    assert old.sender_display_name == "deleted-id"
    assert old.sender_avatar == ""
    assert old.content == "history"
    assert store.get_pm_cursor() == 1
    assert build_store(tmp_path).list() == [old]


def test_group_messages_append_visible_fields_and_page_by_sequence(tmp_path) -> None:
    store = build_store(tmp_path)

    first = store.append(
        message_id="message-1",
        sender_kind="user",
        sender_id="user-a",
        content="check frontend",
        mention_employee_ids=("frontend-a",),
        created_at=100,
    )
    second = store.append(
        message_id="message-2",
        sender_kind="employee",
        sender_display_name="测试员工",
        sender_id="frontend-a",
        content="check complete",
        source_run_id="run-1",
        created_at=200,
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert store.list(after_sequence=1, limit=10) == [second]
    assert store.path.name == "office-group.v1.sqlite3"
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            """
            SELECT sequence, message_id, sender_kind, sender_id, content,
                   mention_employee_ids_json, source_run_id, created_at
            FROM group_messages
            ORDER BY sequence
            """
        ).fetchall()
    assert rows == [
        (1, "message-1", "user", "user-a", "check frontend", '["frontend-a"]', None, 100),
        (2, "message-2", "employee", "frontend-a", "check complete", "[]", "run-1", 200),
    ]
    assert not (store.path.parent / "office-group-messages.v1.jsonl").exists()
    assert not (store.path.parent / "office-group-state.v1.json").exists()


def test_group_messages_concurrent_append_has_unique_monotonic_sequences(tmp_path) -> None:
    store = build_store(tmp_path)

    def append(index: int) -> int:
        return store.append(
            message_id=f"message-{index}",
            sender_kind="user",
            sender_id="user-a",
            content=f"message {index}",
            created_at=index,
        ).sequence

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(append, range(40)))

    assert sorted(sequences) == list(range(1, 41))
    assert [message.sequence for message in store.list(limit=100)] == list(range(1, 41))


def test_group_message_id_is_idempotent_and_conflicts_are_rejected(tmp_path) -> None:
    store = build_store(tmp_path)

    first = store.append(
        message_id="stable-message",
        sender_kind="user",
        sender_id="user-a",
        content="same text",
        mention_employee_ids=("frontend-a",),
        created_at=100,
    )
    repeated = store.append(
        message_id="stable-message",
        sender_kind="user",
        sender_id="user-a",
        content="same text",
        mention_employee_ids=("frontend-a",),
        created_at=999,
    )

    assert repeated == first
    assert len(store.list()) == 1
    with pytest.raises(ValueError, match="already exists with different content"):
        store.append(
            message_id="stable-message",
            sender_kind="user",
            sender_id="user-a",
            content="changed text",
            mention_employee_ids=("frontend-a",),
        )


def test_group_messages_are_user_scoped_and_reject_hidden_payload_fields(tmp_path) -> None:
    first = build_store(tmp_path, "user-a")
    second = build_store(tmp_path, "user-b")

    first.append(message_id="message-a", sender_kind="user", sender_id="user-a", content="visible")

    assert second.list() == []
    with pytest.raises(TypeError):
        first.append(
            message_id="hidden",
            sender_kind="user",
            sender_id="user-a",
            content="visible",
            checkpoint={"id": "forbidden"},
        )


def test_group_messages_report_corrupt_rows_instead_of_skipping_them(tmp_path) -> None:
    store = build_store(tmp_path)
    store.append(
        message_id="message-1",
        sender_kind="user",
        sender_id="user-a",
        content="visible",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE group_messages SET mention_employee_ids_json = ? WHERE message_id = ?",
            ("not-json", "message-1"),
        )

    with pytest.raises(ValueError, match="Corrupt Office group message"):
        store.list()


def test_pm_group_cursor_defaults_to_zero_and_only_moves_forward(tmp_path) -> None:
    store = build_store(tmp_path)

    assert store.get_pm_cursor() == 0
    assert store.advance_pm_cursor(4) == 4
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT integer_value FROM group_state WHERE state_key = ?",
            ("pm_last_group_sequence",),
        ).fetchone()
    assert row == (4,)
    with pytest.raises(ValueError, match="must not move backwards"):
        store.advance_pm_cursor(3)
    assert store.get_pm_cursor() == 4


def test_pm_group_cursor_transaction_failure_keeps_previous_state(tmp_path) -> None:
    store = build_store(tmp_path)
    store.advance_pm_cursor(4)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_pm_cursor_five
            BEFORE UPDATE OF integer_value ON group_state
            WHEN NEW.integer_value = 5
            BEGIN
                SELECT RAISE(ABORT, 'transaction failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="transaction failure"):
        store.advance_pm_cursor(5)
    assert store.get_pm_cursor() == 4


def test_pm_group_cursor_is_user_scoped(tmp_path) -> None:
    first = build_store(tmp_path, "user-a")
    second = build_store(tmp_path, "user-b")

    first.advance_pm_cursor(7)

    assert first.get_pm_cursor() == 7
    assert second.get_pm_cursor() == 0


@pytest.mark.parametrize('kind', ['pm', 'employee'])
def test_employee_public_message_requires_identity_snapshot(tmp_path, kind):
    store = build_store(tmp_path)
    with pytest.raises(ValueError, match='senderDisplayName'):
        store.append(message_id='missing-identity', sender_kind=kind, sender_id='pm', content='公开回复')


def test_canonical_employee_sender_is_shared_by_all_publishers():
    from deerflow.office.identity import employee_sender

    assert employee_sender({'id': 'pm', 'displayName': '项目经理', 'avatar': 'ash', 'canDelegate': True}) == {
        'sender_kind': 'pm', 'sender_id': 'pm', 'sender_display_name': '项目经理', 'sender_avatar': 'ash',
    }
