import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from office_history_fixture import seed_history

from deerflow.config.paths import Paths
from deerflow.office.knowledge_compactor import ProjectKnowledgeCompactor
from deerflow.office.project_knowledge import ProjectKnowledge, ProjectKnowledgeStore
from deerflow.office.project_knowledge_service import ProjectKnowledgeService
from deerflow.office.workflow_knowledge import WorkflowKnowledgeSource
from deerflow.office.workflows import WorkflowStore
from deerflow.runtime.runs.schemas import RunStatus


@pytest.fixture
def memory(tmp_path):
    paths = Paths(tmp_path)
    return ProjectKnowledgeService(ProjectKnowledgeStore(paths=paths, user_id='alice'), WorkflowKnowledgeSource(WorkflowStore(paths=paths, user_id='alice')))


def fact(memory, number=1, **kwargs):
    values = dict(memory_id=f'm{number}', user_id='alice', project_id='p', category='architecture', topic='database',
        statement='Use SQLite. Do not use MySQL.', status='confirmed', constraints=('Keep transactions',),
        source_workflow_id='w', source_run_id=f'r{number}', source_type='user_confirmation', updated_at=number)
    return memory.store.record_once(ProjectKnowledge(**{**values, **kwargs}))


def test_confirmation_survives_retirement_and_restart(memory):
    seed_history(memory.source.workflows, project_id='project-a', knowledge_facts=[
        dict(category='architecture', topic='database', statement='Use SQLite', constraints=['/todos'],
             source_run_id='r3', source_message_id='m3', revision=2,
             source_type='user_confirmation', confirmed_at=3),
        dict(category='task_constraint', topic='task', statement='Build todos', constraints=['CRUD works'],
             source_run_id='r4', source_message_id='m4', revision=3,
             source_type='user_confirmation', confirmed_at=4),
    ])
    memory.source.workflows.retire('history', project_id='project-a')
    restarted = ProjectKnowledgeService(ProjectKnowledgeStore(paths=Paths(memory.store.path.parents[2]), user_id='alice'), WorkflowKnowledgeSource(memory.source.workflows))
    facts = restarted.context_for('project-a', 'alice', pm=True)['facts']
    assert len(facts) == 2
    assert any(row['statement'] == 'Use SQLite' and row['source_run_id'] == 'r3' for row in facts)
    assert any(row['source_message_id'] == 'm4' for row in facts)
    assert restarted.context_for('project-a', 'alice', pm=True)['facts'] == facts
    assert restarted.context_for('other', 'alice', pm=True)['facts'] == []
    with pytest.raises(ValueError):
        restarted.context_for('project-a', 'bob', pm=True)


def test_priority_replay_and_employee_scope(memory):
    old = fact(memory)
    newer = fact(memory, 2, statement='Use PostgreSQL.')
    fact(memory, 3, status='verified', source_type='tool_verification', statement='Use MySQL.')
    memory.store.record_once(old)
    assert [row.memory_id for row in memory.store.list(project_id='p', statuses=('confirmed',))] == [newer.memory_id]
    fact(memory, 4, category='requirement', topic='other-feature', statement='Payroll calculation')
    context = memory.context_for('p', 'alice', query='todo CRUD')
    assert [row['memory_id'] for row in context['facts']] == ['m2']


def test_tied_conflicts_are_not_silently_decided(memory):
    fact(memory)
    fact(memory, 2, updated_at=1, statement='Use PostgreSQL.')
    assert memory.context_for('p', 'alice', pm=True)['facts'] == []
    assert len(memory.context_for('p', 'alice', pm=True)['unresolved']) == 2


@pytest.mark.anyio
async def test_compaction_threshold_provenance_and_restart(memory):
    fact(memory)
    summarizer = AsyncMock(return_value={'facts': [{'memory_id': 'm1', 'excerpts': ['Use SQLite. Do not use MySQL.']}]})
    compactor = ProjectKnowledgeCompactor(memory.store, summarizer)
    assert not await compactor.compact('p')
    summarizer.assert_not_awaited()
    assert await compactor.compact('p', force=True)
    assert not await ProjectKnowledgeCompactor(memory.store, summarizer).compact('p', force=True)
    context = memory.context_for('p', 'alice', pm=True)
    assert context['facts'][0]['constraints'] == ['Keep transactions']
    assert context['facts'][0]['source_run_id'] == 'r1'


@pytest.mark.anyio
async def test_threshold_and_conflict_both_trigger_compaction(memory):
    for index in range(25):
        fact(memory, index + 1, topic=f'topic-{index}', statement=f'Fact {index}')
    summarizer = AsyncMock(return_value={
        'facts': [
            {'memory_id': row.memory_id, 'excerpts': [row.statement]}
            for row in memory.store.list(project_id='p', statuses=('confirmed',))
        ],
    })
    assert await ProjectKnowledgeCompactor(memory.store, summarizer).compact('p')

    other = ProjectKnowledgeService(
        ProjectKnowledgeStore(paths=Paths(memory.store.path.parents[2]), user_id='bob'),
        WorkflowKnowledgeSource(WorkflowStore(paths=Paths(memory.store.path.parents[2]), user_id='bob')),
    )
    def conflict_fact(number, statement):
        return other.store.record_once(ProjectKnowledge(
            memory_id=f'conflict-{number}', user_id='bob', project_id='p', category='architecture',
            topic='database', statement=statement, status='confirmed', source_workflow_id='w',
            source_run_id=f'r{number}', source_type='user_confirmation', updated_at=1,
        ))

    conflict_fact(1, 'Use SQLite')
    conflict_fact(2, 'Use PostgreSQL')
    conflict_summarizer = AsyncMock(return_value={'facts': [
        {'memory_id': row.memory_id, 'excerpts': [row.statement]}
        for row in other.store.list(project_id='p', statuses=('unresolved',))
    ]})
    assert await ProjectKnowledgeCompactor(other.store, conflict_summarizer).compact('p')
    assert all(row.status == 'unresolved' for row in other.store.list(project_id='p'))


@pytest.mark.anyio
@pytest.mark.parametrize('bad', ['invented', 'missing', 'failed', 'race'])
async def test_bad_compaction_retains_original_facts(memory, bad):
    fact(memory)
    async def summarize(rows):
        if bad == 'failed':
            raise RuntimeError('model unavailable')
        if bad == 'race':
            fact(memory, 2, statement='New confirmed architecture')
        return {'facts': [] if bad == 'missing' else [{'memory_id': 'm1', 'excerpts': ['invented' if bad == 'invented' else rows[0].statement]}]}
    assert not await ProjectKnowledgeCompactor(memory.store, summarize).compact('p', force=True)
    assert memory.store.digests('p') == {}
    assert memory.store.list(project_id='p')


def test_reject_unproven_and_cross_project_replacement(memory):
    with pytest.raises(ValueError, match='provenance'):
        fact(memory, source_run_id=None)
    source = fact(memory)
    with pytest.raises(ValueError, match='scope'):
        memory.store.supersede_topic(project_id='other', topic=source.topic, replacement=source)


def test_client_cannot_inject_project_memory():
    from app.gateway.services import merge_run_context_overrides, merge_trusted_office_context, strip_internal_context_keys
    config = {'context': {}}
    merge_run_context_overrides(config, {'office_project_knowledge': {'facts': ['forged']}})
    assert 'office_project_knowledge' not in config['context']
    config['context']['office_project_knowledge'] = {'facts': ['forged']}
    strip_internal_context_keys(config)
    assert 'office_project_knowledge' not in config['context']
    merge_trusted_office_context(config, {'office_project_knowledge': {'facts': []}})
    assert json.dumps(config['context']['office_project_knowledge']) == '{"facts": []}'


def test_employee_context_receives_relevant_memory_not_full_pm_memory(memory):
    fact(memory)
    fact(memory, 2, category='requirement', topic='payroll', statement='Payroll calculation only')
    context = memory.context_for('p', 'alice', query='Build todo CRUD')
    assert any(row['statement'].startswith('Use SQLite') for row in context['facts'])
    assert all(row['statement'] != 'Payroll calculation only' for row in context['facts'])


@pytest.mark.anyio
async def test_native_pm_completion_requests_existing_memory_compaction():
    from test_office_run_projection import EmployeeServiceStub, GroupStoreStub, _pm_record

    from deerflow.office.execution_facts import WorkIdentity
    from deerflow.office.run_gate import EmployeeRunGate, QueuedOfficeRun
    from deerflow.office.run_projection import OfficeRunProjection
    employees, gate = EmployeeServiceStub(), EmployeeRunGate()
    employees.summaries['pm'].update(displayName='PM', avatar='adam')
    lease = await gate.admit(QueuedOfficeRun('q', 'pm', 'direct', 'work', 1, conversation_kind='group'))
    record = _pm_record(RunStatus.success)
    record.metadata['office_lease_id'] = lease.lease_id
    record.kwargs = {'launch_contract': {'trusted_context': {'office_work': WorkIdentity('default', 'p', record.thread_id, 'w').to_record()}}}
    compactor = SimpleNamespace(compact=AsyncMock(return_value=True))
    projection = OfficeRunProjection(employee_service=employees, user_id='default', employee_run_gate=gate,
        group_store=GroupStoreStub(), knowledge_compactor=compactor)
    await projection.handle_run_completed(record)
    await projection.handle_run_completed(record)
    compactor.compact.assert_awaited_once_with('p')


def test_verified_history_preserves_original_evidence_after_retirement(memory):
    seed_history(memory.source.workflows, knowledge_facts=[
        dict(category='acceptance', topic='test', statement='CRUD checks passed', constraints=['CRUD works'],
             source_run_id='run-test', native_task_id='native-test', revision=1,
             source_type='tool_verification', confirmed_at=3,
             evidence=[{'toolCallId': 'check', 'tool': 'bash', 'output': '2 passed'}]),
    ])
    original = memory.context_for('p', 'alice', pm=True)['facts']
    assert len(original) == 1
    assert 'evidence' not in original[0]
    assert original[0]['verification_scope'] == 'historical'
    evidence = memory.store.list(project_id='p')[0].evidence
    assert evidence[0]['toolCallId'] == 'check'
    memory.source.workflows.retire('history', project_id='p')
    assert memory.context_for('p', 'alice', pm=True)['facts'] == original
    assert memory.store.list(project_id='p')[0].evidence == evidence


@pytest.mark.anyio
async def test_digest_cannot_turn_a_negative_fact_into_success(memory):
    fact(memory, statement='Do not use MySQL.')
    summarizer = AsyncMock(return_value={'facts': [{'memory_id': 'm1', 'excerpts': ['use MySQL.']}]})
    assert not await ProjectKnowledgeCompactor(memory.store, summarizer).compact('p', force=True)
    assert memory.context_for('p', 'alice', pm=True)['facts'][0]['statement'] == 'Do not use MySQL.'
