from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from deerflow.runtime.runs.execution import RunExecutionSpecV1


class State(TypedDict):
    value: int


def test_workflow_continues_checkpoint_without_human_answer():
    executed = []

    def finish(state):
        executed.append(state['value'])
        return {'value': state['value'] + 1}

    builder = StateGraph(State)
    builder.add_node('finish', finish)
    builder.add_edge(START, 'finish')
    builder.add_edge('finish', END)
    graph = builder.compile(checkpointer=InMemorySaver(), interrupt_before=['finish'])
    config = {'configurable': {'thread_id': 'workflow-resume'}}
    graph.invoke({'value': 1}, config)
    saved = graph.get_state(config)
    spec = RunExecutionSpecV1(version=1, mode='resume', source_run_id='paused-run',
        checkpoint_id=saved.config['configurable']['checkpoint_id'], resume_payload=None)
    assert graph.invoke(spec.graph_input(), saved.config)['value'] == 2
    assert executed == [1]


def test_human_answer_still_resumes_native_interrupt():
    builder = StateGraph(State)
    builder.add_node('answer', lambda state: {'value': interrupt('请输入数字')})
    builder.add_edge(START, 'answer')
    builder.add_edge('answer', END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': 'human-answer'}}
    graph.invoke({'value': 1}, config)
    saved = graph.get_state(config)
    spec = RunExecutionSpecV1(version=1, mode='resume', source_run_id='question-run',
        checkpoint_id=saved.config['configurable']['checkpoint_id'], resume_payload=0)
    assert graph.invoke(spec.graph_input(), saved.config)['value'] == 0
