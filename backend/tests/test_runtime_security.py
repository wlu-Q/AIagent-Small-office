from __future__ import annotations

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.runtime.security import (
    REDACTED,
    collect_secret_values,
    redact_text,
    redacting_checkpointer,
)

SECRET = "runtime-secret-value"


def test_collect_secret_values_does_not_treat_authorization_config_as_a_secret_tree():
    opaque_secret = 'opaque-provider-secret'
    values = collect_secret_values(
        {
            'authorization': {
                'default_role': 'user',
                'token': SECRET,
            },
            'credentials': {'opaque': opaque_secret},
        }
    )

    assert SECRET in values
    assert opaque_secret in values
    assert 'user' not in values
    assert redact_text('/mnt/user-data/outputs/report.md', values) == (
        '/mnt/user-data/outputs/report.md'
    )


def _checkpoint(checkpoint_id: str) -> dict:
    checkpoint = empty_checkpoint()
    checkpoint["id"] = checkpoint_id
    checkpoint["channel_values"] = {
        "messages": {
            "api_key": SECRET,
            "content": f"provider rejected {SECRET}",
        }
    }
    checkpoint["channel_versions"] = {"messages": "1"}
    return checkpoint


def _config() -> dict:
    return {
        "configurable": {
            "thread_id": "thread-security",
            "checkpoint_ns": "",
        },
        "metadata": {
            "authorization": f"Bearer {SECRET}",
        },
    }


def _assert_checkpoint_is_redacted(saved) -> None:
    rendered = repr(saved)
    assert SECRET not in rendered
    assert REDACTED in rendered
    assert saved.checkpoint["channel_values"]["messages"] == {
        "api_key": REDACTED,
        "content": f"provider rejected {REDACTED}",
    }
    assert saved.metadata["authorization"] == REDACTED
    assert saved.pending_writes == [
        (
            "task-security",
            "tasks",
            {
                "clientSecret": REDACTED,
                "content": f"pending write contains {REDACTED}",
            },
        )
    ]


def test_redacting_checkpointer_scrubs_sync_checkpoint_and_pending_writes():
    saver = InMemorySaver()
    checkpointer = redacting_checkpointer(saver, {SECRET})

    persisted_config = checkpointer.put(
        _config(),
        _checkpoint("checkpoint-sync"),
        {"apiToken": SECRET, "safe": "kept"},
        {"messages": "1"},
    )
    checkpointer.put_writes(
        persisted_config,
        [
            (
                "tasks",
                {
                    "clientSecret": SECRET,
                    "content": f"pending write contains {SECRET}",
                },
            )
        ],
        "task-security",
    )

    saved = saver.get_tuple(persisted_config)
    assert saved is not None
    _assert_checkpoint_is_redacted(saved)


@pytest.mark.anyio
async def test_redacting_checkpointer_scrubs_async_checkpoint_and_pending_writes():
    saver = InMemorySaver()
    checkpointer = redacting_checkpointer(saver, {SECRET})

    persisted_config = await checkpointer.aput(
        _config(),
        _checkpoint("checkpoint-async"),
        {"refreshToken": SECRET, "safe": "kept"},
        {"messages": "1"},
    )
    await checkpointer.aput_writes(
        persisted_config,
        [
            (
                "tasks",
                {
                    "clientSecret": SECRET,
                    "content": f"pending write contains {SECRET}",
                },
            )
        ],
        "task-security",
    )

    saved = await saver.aget_tuple(persisted_config)
    assert saved is not None
    _assert_checkpoint_is_redacted(saved)
