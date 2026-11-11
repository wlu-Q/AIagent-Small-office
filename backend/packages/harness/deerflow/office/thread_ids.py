# 本文件属于 Office 会话身份模块：只生成普通员工私聊、员工群聊和 PM 群聊的确定性 DeerFlow Thread ID。
# 不负责创建 Thread、保存消息、维护上下文、压缩历史或判断 new/resume/interrupt。
# 关联：employees.py 建立员工会话身份，run_launch.py 将该身份交给 DeerFlow 原生 Thread/Run 链。
from __future__ import annotations

from typing import Literal

ConversationKind = Literal["private", "group"]


def employee_thread_id(
    user_id: str,
    employee_id: str,
    kind: ConversationKind,
    *,
    is_pm: bool = False,
) -> str:
    """Return the stable DeerFlow Thread identity for one employee conversation."""

    normalized_user_id = str(user_id or "").strip()
    normalized_employee_id = str(employee_id or "").strip().lower()
    if not normalized_user_id:
        raise ValueError("user_id is required")
    if not normalized_employee_id:
        raise ValueError("employee_id is required")
    if kind not in {"private", "group"}:
        raise ValueError(f"Unsupported conversation kind: {kind}")
    if is_pm and kind == "private":
        raise ValueError("PM does not have a private conversation")
    return f"office__{normalized_user_id}__{normalized_employee_id}__{kind}"
