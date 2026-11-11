import asyncio
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ExtendedModelResponse, ModelResponse
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadState


class OfficeRoundTimeout(GraphBubbleUp):
    def __init__(self, seconds=900):
        super().__init__(f'本轮 Agent 执行超过 {seconds:g} 秒，已停止本轮；不是整个任务的时长限制。')


class RoundClock:
    def __init__(self, seconds, *, consumed=0, clock=time.monotonic):
        self.seconds = seconds
        self.consumed = consumed
        self.clock = clock
        self.active = 0
        self.updated = clock()
        self.lock = threading.RLock()

    def elapsed(self):
        with self.lock:
            now = self.clock()
            if self.active:
                self.consumed += max(0, now - self.updated)
            self.updated = now
            return self.consumed

    def change(self, delta):
        with self.lock:
            self.elapsed()
            self.active += delta

    def check(self):
        if self.elapsed() >= self.seconds:
            raise OfficeRoundTimeout(self.seconds)


_ACTIVITY = ContextVar('office_round_activity', default=None)


@contextmanager
def pause_round_activity():
    activity = _ACTIVITY.get()
    token = _ACTIVITY.set(None)
    if activity is not None:
        activity.change(-1)
    try:
        yield
    finally:
        if activity is not None:
            activity.change(1)
        _ACTIVITY.reset(token)


async def _drain(pending):
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    if not pending.cancelled():
        pending.exception()


async def _sync_call(handler, request):
    pending = asyncio.create_task(asyncio.to_thread(handler, request))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await _drain(pending)
        raise


class OfficeRoundBudgetMiddleware(AgentMiddleware):
    state_schema = ThreadState

    def __init__(self, seconds=900, *, clock=time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self.rounds = {}
        self.lock = threading.RLock()

    def _new_round(self, state):
        with self.lock:
            prior = state.get('office_round_budget') or {}
            self.rounds.pop(prior.get('id'), None)
            identity = uuid.uuid4().hex
            clock = RoundClock(self.seconds, clock=self.clock)
            self.rounds[identity] = clock
            return identity, clock

    def _tool_round(self, state):
        value = state.get('office_round_budget')
        if not value:
            raise ValueError('Office tool execution is missing its model-round budget')
        with self.lock:
            identity = value['id']
            if identity not in self.rounds:
                self.rounds[identity] = RoundClock(self.seconds, consumed=value['consumed'], clock=self.clock)
            return identity, self.rounds[identity]

    async def _run(self, request, handler, clock, *, counted=True):
        clock.check()
        if counted:
            clock.change(1)
        token = _ACTIVITY.set(clock if counted else None)
        pending = asyncio.create_task(handler(request))
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=.05)
                clock.check()
            result = pending.result()
            clock.check()
            return result
        except BaseException as error:
            if isinstance(error, (OfficeRoundTimeout, asyncio.CancelledError)):
                abort = (request.runtime.context or {}).get('abort_event')
                if abort is not None:
                    abort.set()
            pending.cancel()
            await _drain(pending)
            raise
        finally:
            _ACTIVITY.reset(token)
            if counted:
                clock.change(-1)

    @staticmethod
    def _update(identity, clock):
        return {'office_round_budget': {'id': identity, 'consumed': clock.elapsed()}}

    async def awrap_model_call(self, request, handler):
        identity, clock = self._new_round(request.state)
        response = await self._run(request, handler, clock)
        update = self._update(identity, clock)
        if isinstance(response, ExtendedModelResponse):
            command = response.command or Command(update={})
            return replace(response, command=replace(command, update={**(command.update or {}), **update}))
        if isinstance(response, AIMessage):
            response = ModelResponse(result=[response])
        return ExtendedModelResponse(model_response=response, command=Command(update=update))

    def wrap_model_call(self, request, handler):
        return asyncio.run(self.awrap_model_call(request, lambda req: _sync_call(handler, req)))

    async def awrap_tool_call(self, request, handler):
        identity, clock = self._tool_round(request.state)
        counted = request.tool_call['name'] not in {'task', 'ask_clarification'}
        response = await self._run(request, handler, clock, counted=counted)
        update = self._update(identity, clock)
        if isinstance(response, Command):
            return replace(response, update={**(response.update or {}), **update})
        return Command(update={'messages': [response], **update})

    def wrap_tool_call(self, request, handler):
        return asyncio.run(self.awrap_tool_call(request, lambda req: _sync_call(handler, req)))
