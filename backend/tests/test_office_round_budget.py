import asyncio
import threading
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.office_round_budget import (
    OfficeRoundBudgetMiddleware,
    OfficeRoundTimeout,
    RoundClock,
    pause_round_activity,
)
from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from deerflow.agents.thread_state import ThreadState, merge_round_budget
from deerflow.tools.builtins.clarification_tool import ask_clarification_tool


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def request(state=None, name='read_file'):
    return SimpleNamespace(state=state or {}, tool_call={'name': name, 'id': 'call'},
                           runtime=SimpleNamespace(context={'abort_event': threading.Event()}))


def test_round_clock_parallel_time_and_paused_wait_are_not_summed():
    wall = Clock()
    clock = RoundClock(900, clock=wall)
    clock.change(1)
    wall.advance(100)
    clock.change(1)
    wall.advance(200)
    clock.change(-1)
    wall.advance(50)
    clock.change(-1)
    wall.advance(10000)
    assert clock.elapsed() == 350
    clock.change(1)
    wall.advance(550)
    with pytest.raises(OfficeRoundTimeout):
        clock.check()


@pytest.mark.asyncio
async def test_multiround_task_can_exceed_900_but_model_and_tools_share_one_budget():
    wall = Clock()
    budget = OfficeRoundBudgetMiddleware(clock=wall)
    state = {}
    async def model(req):
        wall.advance(500)
        return AIMessage(content='working')
    async def edit(req):
        wall.advance(300)
        return ToolMessage(content='done', tool_call_id='call')
    for _ in range(3):
        response = await budget.awrap_model_call(request(state), model)
        state.update(response.command.update)
        result = await budget.awrap_tool_call(request(state), edit)
        state.update(result.update)
        assert state['office_round_budget']['consumed'] == 800
    assert wall.now == 2400
    with pytest.raises(OfficeRoundTimeout):
        await budget.awrap_tool_call(request(state), edit)


@pytest.mark.asyncio
async def test_task_wait_and_approval_wait_are_exempt_but_other_parallel_tools_are_not():
    wall = Clock()
    budget = OfficeRoundBudgetMiddleware(clock=wall)
    identity, clock = budget._new_round({})
    state = budget._update(identity, clock)
    async def delegate(req):
        wall.advance(10000)
        return ToolMessage(content='employee done', tool_call_id='call')
    await budget.awrap_tool_call(request(state, 'task'), delegate)
    assert clock.elapsed() == 0
    async def approved(req):
        wall.advance(40)
        with pause_round_activity():
            wall.advance(5000)
        wall.advance(60)
        return ToolMessage(content='approved', tool_call_id='call')
    await budget.awrap_tool_call(request(state, 'powershell_run'), approved)
    assert clock.elapsed() == 100
    async def mixed(req):
        clock.change(1)
        try:
            with pause_round_activity():
                wall.advance(850)
        finally:
            clock.change(-1)
        return ToolMessage(content='late', tool_call_id='call')
    with pytest.raises(OfficeRoundTimeout):
        await budget.awrap_tool_call(request(state, 'powershell_run'), mixed)


@pytest.mark.asyncio
async def test_timeout_aborts_and_drains_actual_tool_before_raising():
    budget = OfficeRoundBudgetMiddleware(seconds=.05)
    identity, clock = budget._new_round({})
    req = request(budget._update(identity, clock))
    drained = asyncio.Event()
    async def slow(req):
        try:
            await asyncio.Event().wait()
        finally:
            assert req.runtime.context['abort_event'].is_set()
            await asyncio.sleep(.02)
            drained.set()
    with pytest.raises(OfficeRoundTimeout):
        await budget.awrap_tool_call(req, slow)
    assert drained.is_set()


@pytest.mark.asyncio
async def test_cancel_waiting_task_is_not_timeout():
    budget = OfficeRoundBudgetMiddleware()
    identity, clock = budget._new_round({})
    req = request(budget._update(identity, clock), 'task')
    entered, drained = asyncio.Event(), asyncio.Event()
    async def waiting(req):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()
    pending = asyncio.create_task(budget.awrap_tool_call(req, waiting))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert drained.is_set()


def test_sync_path_enforces_same_budget_and_state_reducer_takes_parallel_max():
    wall = Clock()
    budget = OfficeRoundBudgetMiddleware(clock=wall)
    def model(req):
        wall.advance(899)
        return AIMessage(content='work')
    state = budget.wrap_model_call(request(), model).command.update
    def late(req):
        wall.advance(2)
        return ToolMessage(content='late', tool_call_id='call')
    with pytest.raises(OfficeRoundTimeout):
        budget.wrap_tool_call(request(state), late)
    assert merge_round_budget({'id': 'round', 'consumed': 600}, {'id': 'round', 'consumed': 500})['consumed'] == 600


@pytest.mark.asyncio
async def test_real_graph_checkpoint_resumes_ask_without_counting_wait_or_resetting_spent_time():
    wall = Clock()
    class Spend(AgentMiddleware):
        async def awrap_model_call(self, req, handler):
            wall.advance(400)
            return await handler(req)
    saved = InMemorySaver()
    config = {'configurable': {'thread_id': 'ask-thread'}}
    model = Model(responses=[AIMessage(content='Need info', tool_calls=[{
        'name': 'ask_clarification', 'id': 'ask', 'args': {'question': 'Which page?', 'clarification_type': 'missing_info'},
    }]), AIMessage(content='Received')])
    def graph():
        return create_agent(model=model, tools=[ask_clarification_tool], state_schema=ThreadState,
            middleware=[OfficeRoundBudgetMiddleware(clock=wall), Spend(), ToolErrorHandlingMiddleware(), ClarificationMiddleware()],
            checkpointer=saved)
    first = graph()
    result = await first.ainvoke({'messages': [('user', 'Edit')]}, config)
    assert result['__interrupt__']
    budget_state = (await first.aget_state(config)).values['office_round_budget']
    assert budget_state['consumed'] == 400
    wall.advance(100000)
    second = graph()
    answer = {'version': 1, 'kind': 'human_input_response', 'source': 'ask_clarification', 'request_id': 'clarification:ask',
              'response_kind': 'text', 'value': 'Homepage'}
    final = await second.ainvoke(Command(resume=answer), config)
    assert next(message for message in final['messages'] if isinstance(message, ToolMessage)).content == 'Homepage'
    assert final['messages'][-1].content == 'Received'
    assert final['office_round_budget']['id'] != budget_state['id']
    restored = OfficeRoundBudgetMiddleware(clock=wall)
    identity, clock = restored._tool_round({'office_round_budget': budget_state})
    assert clock.elapsed() == 400
    clock.change(1)
    wall.advance(501)
    with pytest.raises(OfficeRoundTimeout):
        clock.check()


@pytest.mark.asyncio
async def test_real_tool_node_does_not_turn_round_timeout_into_retryable_tool_error():
    wall = Clock()
    @tool
    def slow_tool() -> str:
        """Slow work."""
        wall.advance(901)
        return 'late'
    graph = create_agent(model=Model(responses=[AIMessage(content='', tool_calls=[{
        'name': 'slow_tool', 'id': 'slow', 'args': {},
    }])]), tools=[slow_tool], state_schema=ThreadState,
        middleware=[OfficeRoundBudgetMiddleware(clock=wall), ToolErrorHandlingMiddleware()])
    with pytest.raises(OfficeRoundTimeout):
        await graph.ainvoke({'messages': [('user', 'work')]}, context={'abort_event': threading.Event()})


@pytest.mark.asyncio
async def test_real_approval_wait_pauses_only_its_tool_activity(tmp_path):
    from test_execution_approvals import wait

    from deerflow.config.paths import Paths
    from deerflow.office.execution_approvals import ExecutionApprovals
    from deerflow.office.execution_facts import ExecutionReceiptStore
    wall = Clock()
    budget = OfficeRoundBudgetMiddleware(clock=wall)
    identity, clock = budget._new_round({})
    req = request(budget._update(identity, clock), 'powershell_run')
    approvals = ExecutionApprovals(ExecutionReceiptStore(paths=Paths(tmp_path), user_id='alice'))
    async def command(req):
        wall.advance(100)
        await asyncio.to_thread(wait, approvals, 'command', req.runtime.context['abort_event'].is_set)
        wall.advance(100)
        return ToolMessage(content='approved', tool_call_id='call')
    pending = asyncio.create_task(budget.awrap_tool_call(req, command))
    try:
        for _ in range(200):
            if approvals.pending() and clock.active == 0:
                break
            await asyncio.sleep(.01)
        assert approvals.pending() and clock.active == 0
        wall.advance(10000)
        await asyncio.sleep(.1)
        assert not pending.done()
        value = approvals.pending()[0]
        approvals.decide(value['approval_id'], value['request_hash'], 'approve')
        result = await asyncio.wait_for(pending, 2)
        assert result.update['office_round_budget']['consumed'] == 200
    finally:
        approvals.closed.set()
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending


@pytest.mark.asyncio
async def test_parallel_ask_preserves_completed_sibling_time_across_checkpoint_resume():
    wall = Clock()
    observed = []
    class Spend(AgentMiddleware):
        async def awrap_model_call(self, req, handler):
            observed.append(req.state.get('office_round_budget'))
            wall.advance(400)
            return await handler(req)
    @tool
    def ordinary_work() -> str:
        """Perform ordinary work."""
        wall.advance(100)
        return 'done'
    saved = InMemorySaver()
    config = {'configurable': {'thread_id': 'parallel-ask'}}
    model = Model(responses=[AIMessage(content='', tool_calls=[
        {'name': 'ask_clarification', 'id': 'ask', 'args': {'question': 'Which?', 'clarification_type': 'missing_info'}},
        {'name': 'ordinary_work', 'id': 'work', 'args': {}},
    ]), AIMessage(content='Done')])
    def graph():
        return create_agent(model=model, tools=[ordinary_work, ask_clarification_tool], state_schema=ThreadState,
            middleware=[OfficeRoundBudgetMiddleware(clock=wall), Spend(), ClarificationMiddleware()], checkpointer=saved)
    result = await graph().ainvoke({'messages': [('user', 'work and ask')]}, config)
    assert result['__interrupt__']
    wall.advance(10000)
    answer = {'version': 1, 'kind': 'human_input_response', 'source': 'ask_clarification', 'request_id': 'clarification:ask',
              'response_kind': 'text', 'value': 'Homepage'}
    result = await graph().ainvoke(Command(resume={result['__interrupt__'][0].id: answer}), config)
    assert result['messages'][-1].content == 'Done'
    assert observed[-1]['consumed'] == 500
