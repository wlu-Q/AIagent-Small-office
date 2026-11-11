"""Feed completed Office delegations into the employee's existing memory queue."""

import json
import logging
from collections import Counter
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.memory import get_memory_manager
from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY
from deerflow.config.memory_config import MemoryConfig
from deerflow.office.execution_facts import DelegationIdentity, DelegationReceipt, ExecutionReceiptStore

logger = logging.getLogger(__name__)


def recent_delegation_context(context: dict, *, employee_id: str, user_id: str) -> str:
    from deerflow.config.paths import get_paths
    from deerflow.office.execution_facts import WorkIdentity

    if not context.get('office_work'):
        return ''
    work = WorkIdentity.from_record(context['office_work'])
    actor = context.get('agent_name') or context.get('office_employee_id') or employee_id
    if work.user_id != user_id or actor != employee_id:
        raise ValueError('Recent delegation context ownership mismatch')
    store = ExecutionReceiptStore(paths=get_paths(), user_id=user_id)
    receipts = store.recent_employee_receipts(employee_id=employee_id, project_id=work.project_id)
    facts = []
    for receipt in receipts:
        result = receipt.result_ref or {}
        facts.append(dict(
            recorded_at=receipt.updated_at, status=receipt.status,
            goal=str(receipt.input_ref.get('task', ''))[:240],
            workspace_id=result.get('workspace_id'),
        ))
    if not facts:
        return ''
    return ('\n<recent_delegations>\nRecent same-project assignments to you, from execution receipts. '
            'Goals are requests, not proof of completion. Status is task execution, not acceptance; '
            'These can be newer than asynchronous memory. '
            'Your report and actual tool observations identify your work, not inherited workspace contents. '
            'Do not infer no work from absence in this chat or infer that the user misunderstood.\n'
            + json.dumps(facts, ensure_ascii=False) + '\n</recent_delegations>')


def writeback_evidence(steps: list) -> dict:
    """Return bounded, paired writeback tool facts, not employee completion claims."""
    from deerflow.agents.memory.backends.deermem.deermem.core.prompt import _count_tokens

    calls = set()
    results = {}
    for step in steps:
        if step.get('type') == 'ai':
            calls.update(call['id'] for call in step.get('tool_calls') or []
                         if call.get('name') == 'publish_task_result' and call.get('id'))
        elif step.get('type') == 'tool' and step.get('name') == 'publish_task_result' and step.get('tool_call_id') in calls:
            result = (step.get('artifact') or {}).get('workspace_writeback')
            if result is None:
                continue
            detail = {key: result[key] for key in ('workspace_id', 'status', 'result_commit') if key in result}
            detail['tool_call_id'] = step['tool_call_id']
            for key in ('written_files', 'conflicts'):
                paths = result.get(key, [])
                detail[key] = paths[:12]
                if len(paths) > 12:
                    detail['omitted_' + key] = len(paths) - 12
            if result.get('error'):
                detail['error'] = result['error'][:500]
            results[step['tool_call_id']] = detail
    operations = list(results.values())[-4:]
    summary = dict(total_operations=len(results), operations=operations, omitted_operations=len(results)-len(operations))
    while _count_tokens(json.dumps(summary, ensure_ascii=False)) > 800 and operations:
        operations.pop(0)
        summary['omitted_operations'] += 1
    return summary


def execution_evidence(store, workspace: dict | None, *, work, employee_id: str,
                       run_id: str | None = None, native_task_id: str | None = None, steps: list | None = None) -> dict:
    """Bounded facts from the existing owned command ledger, never command text.

    An optional workspace must have been obtained through ExecutionWorkspaceService.get.
    A DB row is the deduplication unit, so repeated reads cannot erase executions.
    """
    from deerflow.agents.memory.backends.deermem.deermem.core.prompt import _count_tokens

    if store.user_id != work.user_id or (workspace and workspace['project_id'] != work.project_id):
        raise ValueError('Execution evidence ownership mismatch')
    with store._connect() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_executions'").fetchone()
        rows = (db.execute("SELECT state_json FROM command_executions WHERE json_extract(state_json,'$.work_id')=? "
                           "AND json_extract(state_json,'$.employee_id')=?", (work.work_id, employee_id)).fetchall() if exists else [])
    values = [json.loads(row[0]) for row in rows]
    values = [v for v in values if v.get('user_id') == work.user_id and v.get('project_id') == work.project_id
              and v.get('employee_id') == employee_id and v.get('work_id') == work.work_id
              and (run_id is None or v.get('run_id') == run_id)
              and (native_task_id is None or v.get('native_task_id') == native_task_id)]
    from deerflow.office.terminal import ACTIVE

    def priority(value):
        if value.get('purpose') == 'preview_service' and value['status'] in ACTIVE:
            return (0, value.get('created_at', 0))
        return (1 if value['status'] in {'failed', 'timeout', 'unknown', 'cancelled', 'rejected'}
                else 2, value.get('created_at', 0))
    values.sort(key=priority)
    details = []
    for value in values[:6]:
        detail = {key: value.get(key) for key in ('execution_id', 'status', 'exit_code')}
        if value.get('purpose') == 'preview_service':
            detail.update({key: value.get(key) for key in ('purpose', 'resolved_cwd', 'preview_port')})
        details.append(detail)
    summary = dict(project_id=work.project_id, recorded_at=datetime.now(UTC).isoformat(),
                   total_executions=len(values), status_counts=dict(Counter(v['status'] for v in values)),
                   executions=details, omitted_executions=len(values)-len(details),
                   scope='Recorded commands in this task only; not a test-pass conclusion.')
    while _count_tokens(json.dumps(summary, ensure_ascii=False)) > 800 and details:
        details.pop()
        summary['omitted_executions'] += 1
    if steps is not None:
        summary['writebacks'] = writeback_evidence(steps)
    return summary


def with_execution_memory(messages, context, *, employee_id, user_id):
    """Enrich only the existing memory enqueue copy, never graph/chat history."""
    if not context.get('office_work') or not context.get('office_project_path') or context.get('office_native_task_id'):
        return messages  # delegated employees use their terminal receipt instead
    from deerflow.office.execution_workspace import execution_environment
    try:
        service, work, workspace = execution_environment(context)
        if work.user_id != user_id:
            raise ValueError('Memory user differs from workspace owner')
        summary = execution_evidence(service.store, workspace, work=work, employee_id=employee_id,
                                     run_id=context.get('run_id'))
        for i in range(len(messages)-1, -1, -1):
            message = messages[i]
            if getattr(message, 'type', None) == 'ai' and not message.tool_calls:
                enriched = message.model_copy(update={'additional_kwargs': {
                    **message.additional_kwargs, 'office_execution_evidence': summary}})
                return [*messages[:i], enriched, *messages[i+1:]]
    except Exception:
        logger.exception('Direct employee execution evidence unavailable')
    return messages


def delegation_observations(steps: list) -> list[dict]:
    """Keep paired tool identity/status/paths, never interpret file text as status.

    Do not copy source code, shell environment, file bodies or model reasoning
    into long-term memory. A successful write is not independent acceptance.
    """
    calls = {}
    observations = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get('type') == 'ai':
            for call in step.get('tool_calls') or []:
                if isinstance(call, dict) and call.get('id'):
                    calls[call['id']] = call
        elif step.get('type') == 'tool':
            call = calls.pop(step.get('tool_call_id'), None)
            if call is None:
                continue
            meta = (step.get('additional_kwargs') or {}).get(TOOL_META_KEY) or {}
            status = 'error' if step.get('status') == 'error' else meta.get('status', step.get('status', 'unknown'))
            args = call.get('args') or {}
            paths = {key: str(args[key])[:400] for key in ('path', 'file_path', 'directory') if args.get(key)}
            observations.append({'tool': call.get('name'), 'status': status, 'paths': paths,
                                 'tool_call_id': call['id']})
            if call.get('name') == 'apply_patch':
                try:
                    patch_result = json.loads(step.get('content', ''))
                except (ValueError, TypeError):
                    continue
                if isinstance(patch_result, dict) and isinstance(patch_result.get('applied'), list):
                    applied = patch_result['applied']
                    observations[-1]['applied'] = [{key: str(item[key])[:400] for key in ('action', 'path') if key in item}
                                                  for item in applied[:12] if isinstance(item, dict)]
                    observations[-1]['omitted_applied'] = max(0, len(applied) - 12)
                    if args.get('target'):
                        observations[-1]['target'] = args['target']
    return list({item['tool_call_id']: item for item in observations}.values())


def remember_delegation(*, identity: DelegationIdentity, receipt: DelegationReceipt,
                        memory_config: MemoryConfig, trace_id: str | None = None) -> None:
    """Best-effort memory update after a newly observed terminal receipt.

    The native task's first-claim/terminal guards own replay protection. Use the
    receipt key as this update's source so two tasks on one parent Thread cannot
    overwrite each other in the debounced memory queue. This creates no Thread.
    """
    if not memory_config.enabled or memory_config.mode != 'middleware' or receipt.status not in ExecutionReceiptStore.TERMINAL:
        return
    try:
        if receipt.receipt_key != identity.key:
            raise ValueError('Delegation memory identity does not match receipt')
        work = identity.work
        observations = delegation_observations(receipt.evidence)
        if (receipt.result_ref or {}).get('execution_evidence'):
            observations = [item for item in observations if not str(item['tool']).startswith('powershell_')]
        record = {
            'source': 'PM delegation, not a direct user statement',
            'employee_id': receipt.employee_id,
            'project_id': work.project_id,
            'parent_run_id': receipt.parent_run_id,
            'recorded_at': (datetime.fromtimestamp(receipt.updated_at / 1000, UTC)
                            if receipt.updated_at else datetime.now(UTC)).isoformat(),
            'execution_status': receipt.status,
            'execution_evidence': (receipt.result_ref or {}).get('execution_evidence'),
            'workspace': {key: (receipt.result_ref or {}).get(key) for key in
                          ('workspace_id', 'workspace_path', 'baseline_commit')},
            'stop_reason': (receipt.result_ref or {}).get('stop_reason'),
            'task': str(receipt.input_ref.get('task', ''))[:3000],
            'tool_observations': observations[:30],
            'observations_truncated': len(observations) > 30,
        }
        source = f'office-delegation:{identity.key}'
        messages = [
            HumanMessage(content=json.dumps(record, ensure_ascii=False),
                         additional_kwargs={'memory_source': 'pm_delegation'}),
            AIMessage(content=str((receipt.result_ref or {}).get('report') or 'No employee report available.'),
                      additional_kwargs={'memory_source': 'pm_delegation_report'}),
        ]
        get_memory_manager().add(source, messages, agent_name=receipt.employee_id,
                                 user_id=work.user_id, trace_id=trace_id)
    except Exception:
        # Memory extraction must never change task success or block lease cleanup.
        logger.exception('Employee memory enqueue failed: receipt=%s employee=%s', identity.key, receipt.employee_id)
