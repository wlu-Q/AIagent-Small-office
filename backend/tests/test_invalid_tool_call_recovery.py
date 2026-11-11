import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_openai.chat_models.base import _convert_message_to_dict

from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware
from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from deerflow.config.token_budget_config import TokenBudgetConfig
from deerflow.subagents.executor import _extract_final_result


class ProtocolModel(BaseChatModel):
    calls: int = 0
    scenario: str = 'invalid'

    @property
    def _llm_type(self):
        return 'protocol-test'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        wire = [_convert_message_to_dict(message) for message in messages]
        for offset, message in enumerate(wire):
            if not message.get('tool_calls'):
                continue
            replies = []
            for following in wire[offset + 1:]:
                if following['role'] != 'tool':
                    break
                replies.append(following['tool_call_id'])
            assert {call['id'] for call in message['tool_calls']} == set(replies)
        if self.scenario == 'plain':
            response = AIMessage(content='Hello')
        elif self.calls == 1 or self.scenario == 'repeated':
            response = AIMessage(
                content='Preparing to write',
                tool_calls=[{'id': 'valid', 'name': 'record_value', 'args': {'value': 'first'}}] if self.scenario == 'mixed' else [],
                invalid_tool_calls=[{'id': f'bad-{self.calls}', 'name': 'record_value',
                                     'args': '<parameter=value>hello', 'error': 'Invalid JSON'}],
                response_metadata={'finish_reason': 'content_filter' if self.scenario == 'safety' else 'tool_calls'},
                usage_metadata={'input_tokens': 1000, 'output_tokens': 10, 'total_tokens': 1010},
            )
        elif self.calls == 2:
            errors = [message for message in messages if isinstance(message, ToolMessage) and message.status == 'error']
            assert len(errors) == 1
            assert errors[0].tool_call_id == 'bad-1'
            assert '<parameter=value>' not in errors[0].content
            response = AIMessage(content='', tool_calls=[{'id': 'fixed', 'name': 'record_value', 'args': {'value': 'corrected'}}])
        else:
            response = AIMessage(content='Done')
        return ChatResult(generations=[ChatGeneration(message=response)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def graph(scenario, *, budget=False):
    writes = []

    @tool
    def record_value(value: str) -> str:
        """Record a value in memory without changing files."""
        writes.append(value)
        return value

    model = ProtocolModel(scenario=scenario)
    middleware = [ToolErrorHandlingMiddleware(), ModelCallLimitMiddleware(run_limit=4, exit_behavior='error')]
    if budget:
        middleware.append(TokenBudgetMiddleware.from_config(TokenBudgetConfig(max_tokens=1000, enabled=True)))
    middleware.append(SafetyFinishReasonMiddleware())
    return create_agent(model, [record_value], middleware=middleware), model, writes


@pytest.mark.parametrize('scenario,expected', [('invalid', ['corrected']), ('mixed', ['first', 'corrected']), ('plain', [])])
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_native_loop_recovers_only_invalid_arguments(scenario, expected, asynchronous):
    agent, model, writes = graph(scenario)
    state = {'messages': [HumanMessage(content='Please do this task')]}
    result = await agent.ainvoke(state) if asynchronous else agent.invoke(state)
    assert writes == expected
    assert model.calls == (1 if scenario == 'plain' else 3)
    assert result['messages'][-1].content == ('Hello' if scenario == 'plain' else 'Done')


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_repeated_invalid_calls_obey_existing_call_limit(asynchronous):
    agent, model, writes = graph('repeated')
    with pytest.raises(ModelCallLimitExceededError):
        state = {'messages': [HumanMessage(content='Please do this task')]}
        if asynchronous:
            await agent.ainvoke(state, config={'recursion_limit': 60})
        else:
            agent.invoke(state, config={'recursion_limit': 60})
    assert model.calls == 4
    assert writes == []


@pytest.mark.parametrize('scenario,budget', [('invalid', True), ('safety', False)])
def test_existing_stop_cannot_be_reopened_by_protocol_recovery(scenario, budget):
    agent, model, writes = graph(scenario, budget=budget)
    result = agent.invoke({'messages': [HumanMessage(content='Please do this task')]})
    assert model.calls == 1
    assert writes == []
    assert not result['messages'][-1].invalid_tool_calls


def test_invalid_terminal_response_is_not_a_successful_preparation_report():
    message = AIMessage(content='Preparing to write', invalid_tool_calls=[
        {'id': 'bad', 'name': 'apply_patch', 'args': '<parameter=patch>', 'error': 'Invalid JSON'},
    ])
    with pytest.raises(ValueError, match='Invalid tool call'):
        _extract_final_result({'messages': [message]}, trace_id='test', name='employee')


def test_missing_call_identity_is_not_replaced_with_invented_identity():
    message = AIMessage(content='', invalid_tool_calls=[{'id': None, 'name': 'apply_patch', 'args': '{'}])
    with pytest.raises(ValueError, match='call ID'):
        ToolErrorHandlingMiddleware().after_model({'messages': [message]}, None)
