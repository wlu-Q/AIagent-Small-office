from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from deerflow.config.paths import Paths
from deerflow.office.project_knowledge import ProjectKnowledge, ProjectKnowledgeStore


def test_project_knowledge_is_scoped_and_new_topic_supersedes_old(tmp_path: Path):
    paths = Paths(tmp_path / '.deer-flow')
    store = ProjectKnowledgeStore(paths=paths, user_id='alice')
    common = dict(user_id='alice', project_id='project-a', category='architecture', topic='database',
                  status='confirmed', source_workflow_id='workflow-a', source_type='user_confirmation')
    old = store.record_once(ProjectKnowledge(memory_id='old', statement='Use A', source_run_id='run-a', updated_at=10, **common))
    replacement = store.record_once(ProjectKnowledge(memory_id='new', statement='Use B', source_run_id='run-b', updated_at=20, **common))
    rows = store.list(project_id='project-a', topic='database')
    assert replacement.supersedes == (old.memory_id,)
    assert any(item.status == 'superseded' and item.memory_id == old.memory_id for item in rows)
    assert store.list(project_id='project-b') == ()
    assert ProjectKnowledgeStore(paths=paths, user_id='bob').list(project_id='project-a') == ()


def test_replayed_fact_is_atomic_and_idempotent(tmp_path: Path):
    store = ProjectKnowledgeStore(paths=Paths(tmp_path), user_id='alice')
    fact = ProjectKnowledge(
        memory_id='stable-source', user_id='alice', project_id='project-a',
        category='architecture', topic='database', statement='Use SQLite',
        status='confirmed', source_workflow_id='workflow-a', source_run_id='run-a',
        source_type='user_confirmation', updated_at=10,
    )
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: store.record_once(fact), range(12)))
    assert {row.memory_id for row in rows} == {'stable-source'}
    assert len(store.list(project_id='project-a')) == 1


def standalone(tmp_path):
    from deerflow.office.project_knowledge_service import ProjectKnowledgeService
    return ProjectKnowledgeService(ProjectKnowledgeStore(paths=Paths(tmp_path), user_id='alice'))


def test_effective_fact_without_workflow_requires_real_provenance(tmp_path):
    service = standalone(tmp_path)
    fact = service.capture_user_message(project_id='p', user_id='alice', run_id='r', message_id='m',
        text='Use SQLite for this project.', category='architecture', topic='database', scope='project', occurred_at=10)
    assert fact.source_workflow_id is None and fact.source_message_id == 'm'
    assert fact.source_type == 'user_statement'
    restarted = standalone(tmp_path)
    assert restarted.context_for('p', 'alice', pm=True)['facts'][0]['statement'] == 'Use SQLite for this project.'
    with pytest.raises(ValueError):
        replace(fact, source_message_id=None)
    with pytest.raises(ValueError):
        replace(fact, source_type='model_proposal')
    with pytest.raises(ValueError):
        service.capture_user_message(project_id='p', user_id='bob', run_id='r', message_id='m', text='Secret',
            category='architecture', topic='database', scope='project', occurred_at=10)


def test_partial_approval_does_not_confirm_whole_proposal(tmp_path):
    service = standalone(tmp_path)
    text = 'Use SQLite. The cache design is not approved.'
    fact = service.capture_user_message(project_id='p', user_id='alice', run_id='r', message_id='m',
        text=text, category='architecture', topic='database', scope='project', occurred_at=10)
    assert fact.statement == text
    assert 'not approved' in service.context_for('p', 'alice', pm=True)['facts'][0]['statement']
    # No proposal argument and no paraphrase: the exact user words are retained.
    assert service.capture_user_message(project_id='p', user_id='alice', run_id='r', message_id='task-m',
        text='Only change this screen', category='requirement', topic='screen', scope='task', occurred_at=11) is None
    assert len(service.store.list(project_id='p')) == 1


def test_stage_free_source_replay_and_conflict(tmp_path):
    service = standalone(tmp_path)
    args = dict(project_id='p', user_id='alice', run_id='r', category='architecture', topic='database',
                scope='project', occurred_at=10)
    service.capture_user_message(message_id='m1', text='Use SQLite.', **args)
    service.capture_user_message(message_id='m2', text='Use PostgreSQL.', **args)
    service.capture_user_message(message_id='m1', text='Use SQLite.', **args)
    assert len(service.context_for('p', 'alice', pm=True)['unresolved']) == 2
    with pytest.raises(ValueError):
        service.capture_user_message(message_id='m1', text='Changed source text', **args)
    assert len(service.store.list(project_id='p')) == 2


def test_historical_evidence_is_stored_but_not_reinjected_into_model_context(tmp_path):
    service = standalone(tmp_path / 'state')
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'home.vue').write_text('first', encoding='utf-8')
    steps = [{'type':'ai', 'tool_calls':[{'id':'c', 'name':'bash', 'args':{'command':'test'}}]},
             {'type':'tool', 'tool_call_id':'c', 'content':'2 passed'}]
    fact = service.capture_verification(project_id='p', user_id='alice', run_id='r', native_task_id='task',
        topic='filter-tests', steps=steps, occurred_at=10)
    assert fact.source_workflow_id is None and fact.native_task_id == 'task'
    assert service.context_for('p', 'alice', query='filter-tests')['facts'], 'Task-scoped lookup must retain related evidence'
    (project / 'home.vue').write_text('new implementation', encoding='utf-8')
    view = service.context_for('p', 'alice', pm=True)['facts'][0]
    assert view['verification_scope'] == 'historical'
    assert 'evidence' not in view
    assert view['statement'] == fact.statement
    assert view['source_run_id'] == 'r' and view['updated_at'] == 10
    assert standalone(tmp_path / 'state').store.list(project_id='p')[0].evidence == fact.evidence
    assert fact.evidence == ({'toolCallId': 'c', 'tool': 'bash', 'summary': '2 passed'},)
    assert 'evidence' not in service.context_for('p', 'alice', query='filter-tests')['facts'][0]


def test_model_view_keeps_user_constraints_conflicts_and_legacy_observations(tmp_path):
    from deerflow.office.project_knowledge_service import knowledge_prompt
    from deerflow.office.work_context import build_delegation_prompt

    service = standalone(tmp_path)
    history = ProjectKnowledge(memory_id='history', user_id='alice', project_id='p', category='acceptance',
        topic='filter-checks', statement='Filter was checked yesterday; not a current pass.', status='verified',
        source_type='tool_verification', source_run_id='run-history', native_task_id='task-history', updated_at=10,
        evidence=({'toolCallId': 'read', 'tool': 'read_file', 'summary': 'RAW_SOURCE_SENTINEL' * 200},
                  {'type': 'project_snapshot', 'fingerprint': 'old-persisted-fingerprint'}))
    service.capture([history])
    for message, text in [('first', 'Do not remove the old filter.'), ('second', 'Remove the old filter.')]:
        service.capture_user_message(project_id='p', user_id='alice', run_id='run', message_id=message,
            text=text, category='constraint', topic='filter', scope='project', occurred_at=11)
    context = service.context_for('p', 'alice', pm=True)
    prompt = knowledge_prompt(context)
    delegated = build_delegation_prompt(task_prompt='filter', shared_context={
        'user_id': 'alice', 'project_id': 'p', 'thread_id': 'thread', 'work_id': 'work', 'latest_user_message': 'filter'}, project_knowledge=context)
    for view in (prompt, delegated):
        assert 'RAW_SOURCE_SENTINEL' not in view and 'old-persisted-fingerprint' not in view
        assert 'not a current pass' in view and 'Do not remove the old filter.' in view
        assert 'Remove the old filter.' in view and 'unresolved' in view
        assert 'run-history' in view and 'task-history' in view
    assert service.store.list(project_id='p', topic='filter-checks')[0].evidence == history.evidence


@pytest.mark.parametrize('steps', [[], [{'type':'tool','tool_call_id':'invented','content':'passed'}],
    [{'type':'ai','tool_calls':[{'id':'c','name':'bash'}]}, {'type':'tool','tool_call_id':'c','content':'2 failed'}],
    [{'type':'ai','tool_calls':[{'id':'c','name':'bash'}]}, {'type':'tool','tool_call_id':'c','content':'2 passed'},
     {'type':'ai','tool_calls':[{'id':'c','name':'bash'}]}, {'type':'tool','tool_call_id':'c','content':'3 passed'}]])
def test_verification_rejects_absent_or_failed_tool_trace(tmp_path, steps):
    service = standalone(tmp_path)
    with pytest.raises(ValueError):
        service.capture_verification(project_id='p', user_id='alice', run_id='r', native_task_id='task',
            topic='tests', steps=steps, occurred_at=10)
    assert service.store.list(project_id='p') == ()


@pytest.mark.parametrize('status,meta', [('error', {}), ('success', {'status': 'error'})])
def test_verification_rejects_structured_read_failure_without_error_text(tmp_path, status, meta):
    service = standalone(tmp_path)
    steps = [{'type': 'ai', 'tool_calls': [{'id': 'c', 'name': 'read_file'}]},
             {'type': 'tool', 'tool_call_id': 'c', 'status': status, 'content': 'ordinary text',
              'additional_kwargs': {'deerflow_tool_meta': meta}}]
    with pytest.raises(ValueError):
        service.capture_verification(project_id='p', user_id='alice', run_id='r', native_task_id='task',
            topic='read', steps=steps, occurred_at=10)
    assert service.store.list(project_id='p') == ()
