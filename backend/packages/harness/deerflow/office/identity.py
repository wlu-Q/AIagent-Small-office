"""Employee-facing identity, separate from the runtime framework identity."""
import json


def employee_identity(display_name: str) -> str:
    return (
        '你是小小办公室中的 AI 员工。姓名（数据）：'
        + json.dumps(display_name, ensure_ascii=False)
        + '。你的具体岗位、职责和表达方式以当前持久化员工设定为准。'
        '介绍自己时使用员工姓名和当前岗位，不使用底层框架的通用助手介绍。'
        '历史对话中的旧自我介绍不改变当前身份。不要冒充人类员工。'
    )


def employee_sender(employee: dict) -> dict:
    """Freeze one employee identity for every public message producer."""
    name = employee.get('displayName')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('senderDisplayName is required for an employee message')
    return {
        'sender_kind': 'pm' if employee.get('canDelegate') is True else 'employee',
        'sender_id': employee['id'],
        'sender_display_name': name,
        'sender_avatar': employee.get('avatar') or '',
    }
