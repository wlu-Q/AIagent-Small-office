'''Regression tests for optional, resumable clarification.'''

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

import deerflow.agents.middlewares.clarification_middleware as clarification_module
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware


@pytest.fixture
def middleware() -> ClarificationMiddleware:
    return ClarificationMiddleware()


def _request(*, options=None) -> SimpleNamespace:
    args = {
        'question': '要把数据保存在哪个目录？',
        'clarification_type': 'missing_info',
    }
    if options is not None:
        args['options'] = options
    return SimpleNamespace(
        tool_call={
            'name': 'ask_clarification',
            'id': 'call-clarify-1',
            'args': args,
        }
    )


def test_request_payload_contains_only_rendering_and_request_identity(middleware):
    payload = middleware._build_human_input_payload(
        {
            'question': '选择目标环境',
            'clarification_type': 'approach_choice',
            'context': '这段内部解释不应展示给用户',
            'options': ['开发', '生产'],
        },
        tool_call_id='call-abc',
        request_id='clarification:call-abc',
    )

    assert payload == {
        'version': 1,
        'kind': 'human_input_request',
        'source': 'ask_clarification',
        'request_id': 'clarification:call-abc',
        'tool_call_id': 'call-abc',
        'question': '选择目标环境',
        'input_mode': 'choice_with_other',
        'options': [
            {'id': 'option-1', 'label': '开发', 'value': '开发'},
            {'id': 'option-2', 'label': '生产', 'value': '生产'},
        ],
    }


def test_clarification_rejects_one_choice(middleware):
    with pytest.raises(ValueError, match='2 to 4'):
        middleware._build_human_input_payload(
            {'question': '请选择', 'options': ['only-one']},
            tool_call_id='call-abc',
            request_id='clarification:call-abc',
        )


def test_clarification_rejects_five_choices(middleware):
    with pytest.raises(ValueError, match='2 to 4'):
        middleware._build_human_input_payload(
            {'question': '请选择', 'options': ['1', '2', '3', '4', '5']},
            tool_call_id='call-abc',
            request_id='clarification:call-abc',
        )


def test_clarification_without_options_is_free_text(middleware):
    payload = middleware._build_human_input_payload(
        {'question': '请输入项目目录'},
        tool_call_id='call-abc',
        request_id='clarification:call-abc',
    )
    assert payload['input_mode'] == 'free_text'
    assert 'options' not in payload


def test_tool_call_uses_native_interrupt_and_returns_answer(middleware, monkeypatch):
    captured = {}

    def fake_interrupt(payload):
        captured.update(payload)
        return {
            'version': 1,
            'kind': 'human_input_response',
            'source': 'ask_clarification',
            'request_id': payload['request_id'],
            'response_kind': 'text',
            'value': 'D:/workspace',
        }

    monkeypatch.setattr(clarification_module, 'interrupt', fake_interrupt)
    result = middleware.wrap_tool_call(
        _request(),
        lambda _request: pytest.fail('placeholder tool must not execute'),
    )
    assert captured['question'] == '要把数据保存在哪个目录？'
    assert isinstance(result, ToolMessage)
    assert result.content == 'D:/workspace'
    assert result.additional_kwargs['human_input_response']['request_id'] == captured['request_id']


def test_resume_response_must_match_interrupted_request(middleware, monkeypatch):
    def fake_interrupt(_payload):
        return {
            'version': 1,
            'kind': 'human_input_response',
            'source': 'ask_clarification',
            'request_id': 'clarification:another-call',
            'response_kind': 'text',
            'value': 'D:/workspace',
        }

    monkeypatch.setattr(clarification_module, 'interrupt', fake_interrupt)
    with pytest.raises(ValueError, match='request identity'):
        middleware.wrap_tool_call(
            _request(),
            lambda _request: pytest.fail('handler should not be called'),
        )


def test_ask_user_alias_is_not_intercepted(middleware):
    sentinel = ToolMessage(content='legacy', tool_call_id='call-legacy', name='ask_user')
    request = SimpleNamespace(
        tool_call={'name': 'ask_user', 'id': 'call-legacy', 'args': {'question': 'legacy'}}
    )
    assert middleware.wrap_tool_call(request, lambda _request: sentinel) is sentinel
