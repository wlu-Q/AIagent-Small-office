# 本文件属于 Office 会话身份测试模块：只验证普通员工私聊、员工群聊和 PM 群聊 Thread ID 规则。
# 不负责 Thread 创建、上下文压缩、消息保存、Run new/resume/interrupt 或 Checkpoint 测试。
# 关联：office/thread_ids.py 是被测身份生成器，run_launch.py 将结果交给 DeerFlow。
from __future__ import annotations

import pytest

from deerflow.office.thread_ids import employee_thread_id


def test_employee_thread_id_builds_stable_private_and_group_ids() -> None:
    assert (
        employee_thread_id("default", "frontend-a", "private")
        == "office__default__frontend-a__private"
    )
    assert (
        employee_thread_id("default", "frontend-a", "group")
        == "office__default__frontend-a__group"
    )


def test_employee_thread_id_rejects_pm_private_thread() -> None:
    with pytest.raises(ValueError, match="PM does not have a private conversation"):
        employee_thread_id("default", "pm", "private", is_pm=True)

    assert (
        employee_thread_id("default", "pm", "group", is_pm=True)
        == "office__default__pm__group"
    )


@pytest.mark.parametrize(
    ("user_id", "employee_id", "kind"),
    [
        ("", "frontend-a", "private"),
        ("default", "", "private"),
        ("default", "frontend-a", "unknown"),
    ],
)
def test_employee_thread_id_rejects_invalid_identity_parts(
    user_id: str,
    employee_id: str,
    kind: str,
) -> None:
    with pytest.raises(ValueError):
        employee_thread_id(user_id, employee_id, kind)  # type: ignore[arg-type]
