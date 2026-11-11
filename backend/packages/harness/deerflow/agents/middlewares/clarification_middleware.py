'''Optional clarification implemented with LangGraph native interrupts.'''

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command, interrupt

from deerflow.agents.human_input import parse_human_input_response

_CLARIFICATION_TOOL_NAME = 'ask_clarification'


class ClarificationMiddlewareState(AgentState):
    '''Compatible with the ThreadState schema.'''

    pass


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    '''Pause only when the model explicitly requests missing user input.'''

    state_schema = ClarificationMiddlewareState

    @staticmethod
    def _stable_request_id(tool_call_id: str, question: str) -> str:
        if tool_call_id:
            return f'clarification:{tool_call_id}'
        digest = sha256(question.encode('utf-8')).hexdigest()[:16]
        return f'clarification:{digest}'

    @staticmethod
    def _normalize_options(raw_options: Any) -> list[str]:
        options = raw_options
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except (json.JSONDecodeError, TypeError):
                options = [options]
        if options is None:
            return []
        if not isinstance(options, list):
            options = [options]
        normalized = [str(option).strip() for option in options if str(option).strip()]
        if normalized and not 2 <= len(normalized) <= 4:
            raise ValueError('Clarification options must contain 2 to 4 choices')
        return normalized

    def _build_human_input_payload(
        self,
        args: dict[str, Any],
        *,
        tool_call_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        question = str(args.get('question') or '').strip()
        if not question:
            raise ValueError('Clarification question must be non-empty')
        options = self._normalize_options(args.get('options'))
        payload: dict[str, Any] = {
            'version': 1,
            'kind': 'human_input_request',
            'source': _CLARIFICATION_TOOL_NAME,
            'request_id': request_id,
            'question': question,
            'input_mode': 'choice_with_other' if options else 'free_text',
        }
        if tool_call_id:
            payload['tool_call_id'] = tool_call_id
        if options:
            payload['options'] = [
                {
                    'id': f'option-{index}',
                    'label': option,
                    'value': option,
                }
                for index, option in enumerate(options, 1)
            ]
        return payload

    def _handle_clarification(self, request: ToolCallRequest) -> ToolMessage:
        args = request.tool_call.get('args', {})
        if not isinstance(args, dict):
            raise ValueError('Clarification arguments must be an object')
        tool_call_id = str(request.tool_call.get('id') or '')
        question = str(args.get('question') or '').strip()
        request_id = self._stable_request_id(tool_call_id, question)
        payload = self._build_human_input_payload(
            args,
            tool_call_id=tool_call_id,
            request_id=request_id,
        )
        response = parse_human_input_response(interrupt(payload))
        if response is None:
            raise ValueError('Clarification resume payload is invalid')
        if (
            response['source'] != _CLARIFICATION_TOOL_NAME
            or response['request_id'] != request_id
        ):
            raise ValueError('Clarification response does not match request identity')
        if response['response_kind'] == 'option':
            option_id = response['option_id']
            valid_options = {
                option['id']: option['value']
                for option in payload.get('options', [])
            }
            if valid_options.get(option_id) != response['value']:
                raise ValueError('Clarification response option does not match request')
        return ToolMessage(
            content=response['value'],
            tool_call_id=tool_call_id,
            name=_CLARIFICATION_TOOL_NAME,
            additional_kwargs={
                'hide_from_ui': True,
                'human_input_response': response,
            },
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get('name') != _CLARIFICATION_TOOL_NAME:
            return handler(request)
        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get('name') != _CLARIFICATION_TOOL_NAME:
            return await handler(request)
        return self._handle_clarification(request)
