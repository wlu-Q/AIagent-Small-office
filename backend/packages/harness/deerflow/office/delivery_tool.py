"""Explicit employee delivery tool, sharing native tool status and Office policy."""
import json
from dataclasses import asdict

from langchain.tools import tool
from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY, ToolResultMeta
from deerflow.tools.types import Runtime


@tool('publish_task_result', parse_docstring=True)
def publish_task_result(runtime: Runtime, source_workspace_ids: list[str] | None = None, target: str | None = None) -> ToolMessage:
    """Merge an existing worktree into the user's bound project, preserving their index and edits.

    target selects the worktree doing the merge, even if this task runs in the bound
    project. Merge optional source workspaces into it and write back to the project;
    do not copy/reimplement their files. No test IDs or service shutdown are required. Real Git conflicts stay
    in the returned workspace: resolve and stage them with Git before calling again,
    or abort the merge. Written files do not prove tests passed.
    Approval uses the Office UI, not ask_clarification.
    Report rejection without retrying. The user's HEAD and index are not changed.

    Args:
        source_workspace_ids: Other workspace IDs from this project to merge into the target; omit if none.
        target: Existing workspace ID doing the merge; omit only when this task already runs in that worktree.
    """
    from deerflow.office.execution_workspace import execution_environment
    from deerflow.office.workspace_delivery import publish
    from deerflow.sandbox import tools
    stop = False
    try:
        service, work, value = execution_environment(runtime.context or {}, target=target)
        if not value['workspace_id']:
            raise ValueError('Supply target=<existing workspace ID> to integrate its work. Direct project edits need no writeback.')
        result = publish(service, work, value['workspace_id'], context=runtime.context,
                         source_workspace_ids=source_workspace_ids or [])
        failed = result['status'] == 'conflict'
    except Exception as exc:
        message = tools.mask_local_paths_in_output(str(exc), runtime.state.get('thread_data'))
        result = {'status': 'error', 'error': tools.mask_secret_values(message, tools.read_active_secrets(runtime.context))[:2000]}
        failed = True
        stop = isinstance(exc, PermissionError)
    meta = ToolResultMeta(status='error' if failed else 'success', error_type='delivery_incomplete' if failed else None,
                         recoverable_by_model=not stop, recommended_next_action='stop' if stop else 'try_alternative' if failed else 'continue', source='tool_return')
    return ToolMessage(content=json.dumps(result, ensure_ascii=False), name='publish_task_result',
        tool_call_id=runtime.tool_call_id, status='error' if failed else 'success', additional_kwargs={TOOL_META_KEY: asdict(meta)},
        artifact={'workspace_writeback': result})


from deerflow.sandbox.powershell_tools import _async_adapter
publish_task_result.coroutine = _async_adapter(publish_task_result.func)
