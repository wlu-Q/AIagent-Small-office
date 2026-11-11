"""Native task provenance and durable replay protection, with no business phases."""
import hashlib
import json
from dataclasses import asdict

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.config.paths import get_paths
from deerflow.office.execution_facts import DelegationIdentity, ExecutionReceiptStore, WorkIdentity
from deerflow.office.work_context import build_delegation_prompt
from deerflow.utils.tool_arguments import normalize_tool_arguments


def _delegation_request(runtime, *, user_id, employee_id, description, prompt, subagent_type, tool_call_id,
                        workspace_mode='current', workspace_id=None, required_tools=None):
    context = runtime.context
    work = WorkIdentity.from_record(context.get('office_work'))
    thread_id = context.get('thread_id') or runtime.config.get('configurable', {}).get('thread_id')
    if (work.user_id, work.project_id, work.thread_id) != (user_id, context.get('office_project_id'), thread_id):
        raise ValueError('Delegation work ownership mismatch')
    metadata = runtime.config.get('metadata', {})
    if metadata.get('office_employee_id') != 'pm':
        raise ValueError('Only the Office PM may delegate')
    message = next((m for m in reversed(runtime.state.get('messages', []))
                    if isinstance(m, AIMessage) and any(c.get('id') == tool_call_id for c in m.tool_calls)), None)
    if message is None or not message.id:
        raise ValueError('Delegation requires a persisted AI message and tool call identity')
    call = next(c for c in message.tool_calls if c.get('id') == tool_call_id)
    args, _ = normalize_tool_arguments(call.get('name'), call.get('args', {}))
    if args.get('workspace_mode', 'current') != workspace_mode or args.get('workspace_id') != workspace_id:
        raise ValueError('Workspace selection differs from the persisted tool call')
    if call.get('name') != 'task' or any(args.get(k) != v for k, v in dict(description=description, prompt=prompt, subagent_type=subagent_type).items()):
        raise ValueError('Delegation arguments do not match the persisted AI tool call')
    if (args.get('required_tools') or []) != (required_tools or []):
        raise ValueError('Required tools differ from the persisted tool call')
    background = context.get('office_work_context')
    if WorkIdentity.from_record(background) != work:
        raise ValueError('Delegation background ownership mismatch')
    identity = DelegationIdentity(work, message.id, tool_call_id)
    request_hash = hashlib.sha256(json.dumps({'args': args, 'employee_id': employee_id, 'work': asdict(work)},
                                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    store = ExecutionReceiptStore(paths=get_paths(), user_id=user_id)
    return store, identity, request_hash


def lookup_delegation(runtime, **request):
    store, identity, request_hash = _delegation_request(runtime, **request)
    receipt = store.get(identity)
    if receipt is not None and (receipt.employee_id != request['employee_id'] or receipt.request_hash != request_hash):
        raise ValueError('Delegation identity already belongs to a different request')
    return receipt


def recover_task_results(work_record, calls):
    from deerflow.office.execution_workspace import result_reference
    from deerflow.subagents.status_contract import format_subagent_result_message, make_subagent_additional_kwargs

    work = WorkIdentity.from_record(work_record)
    store = ExecutionReceiptStore(paths=get_paths(), user_id=work.user_id)
    receipts = store.terminal_call_receipts(project_id=work.project_id, thread_id=work.thread_id,
                                           run_ids=sorted({run_id for _, _, run_id in calls}))
    statuses = {'succeeded': 'completed', 'failed': 'failed', 'interrupted': 'cancelled',
                'timeout': 'timed_out', 'unknown': 'unknown'}
    recovered = {}
    for message_id, call_id, run_id in calls:
        receipt = receipts.get((run_id, call_id))
        if receipt is None or receipt.input_ref.get('ai_message_id') != message_id:
            continue
        result = receipt.task_result()
        status = statuses[receipt.status]
        metadata = make_subagent_additional_kwargs(status, result=result.get('report'), stop_reason=result.get('stop_reason'))
        content, _ = format_subagent_result_message(status, result=metadata.get('subagent_result_brief'),
                                                   stop_reason=result.get('stop_reason'))
        facts = result_reference({key: value for key, value in result.items() if key != 'root_path'})
        content += '\nTask workspace facts: ' + json.dumps(facts, ensure_ascii=False)
        recovered[(message_id, call_id)] = ToolMessage(
            content=content, tool_call_id=call_id, name='task',
            additional_kwargs={**metadata, 'office_execution_workspace': facts})
    return recovered


def claim_delegation(runtime, *, user_id, employee_id, description, prompt, subagent_type, tool_call_id,
                     package_snapshot=None, workspace_mode='current', workspace_id=None, required_tools=None):
    store, identity, request_hash = _delegation_request(
        runtime, user_id=user_id, employee_id=employee_id, description=description, prompt=prompt,
        subagent_type=subagent_type, tool_call_id=tool_call_id, workspace_mode=workspace_mode,
        workspace_id=workspace_id, required_tools=required_tools)
    context = runtime.context
    work = identity.work
    metadata = runtime.config.get('metadata', {})
    background = context.get('office_work_context')
    knowledge = context.get('office_project_knowledge') or {}
    employee_prompt = build_delegation_prompt(task_prompt=prompt, shared_context=background, project_knowledge=knowledge)
    workspace_ref = None
    from deerflow.office.execution_workspace import ExecutionWorkspaceService, WORKSPACE_CONTEXT_KEY
    if workspace_mode not in {'current', 'new', 'continue', 'fork'}:
        raise ValueError('Unknown workspace mode')
    if (workspace_mode in {'continue', 'fork'}) != bool(workspace_id):
        raise ValueError('Continue/fork requires a workspace_id; current/new does not')
    mode = workspace_mode
    if mode == 'current' and context.get(WORKSPACE_CONTEXT_KEY):
        mode, workspace_id = 'continue', context[WORKSPACE_CONTEXT_KEY]['workspace_id']
    if mode != 'current':
        if not context.get('office_project_path'):
            raise ValueError('A worktree requires a bound project')
        workspace_ref = ExecutionWorkspaceService(user_id=user_id).allocate(
            work, employee_id=employee_id, project_path=context['office_project_path'],
            owner_key=identity.key, mode=mode, source_workspace_id=workspace_id)
    receipt, first = store.claim(identity, employee_id=employee_id, request_hash=request_hash,
                                parent_run_id=context.get('run_id'), input_ref={
                                    'run_id': context.get('run_id'), 'ai_message_id': identity.ai_message_id,
                                    'checkpoint_id': runtime.config.get('configurable', {}).get('checkpoint_id'),
                                    'task': prompt,
                                    'workspace': workspace_ref,
                                    'workspace_mode': workspace_mode,
                                    'background': background, 'knowledge': knowledge,
                                    'registry': metadata.get('office_employee_registry_snapshot'),
                                    'selected_employee_ids': metadata.get('office_preselected_employee_ids') or [],
                                    'employee_package': package_snapshot or {},
                                })
    return store, identity, receipt, first, employee_prompt
