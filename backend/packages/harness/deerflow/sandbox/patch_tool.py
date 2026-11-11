"""LangChain patch entry point; path and lifecycle authority stay in Office."""
import json
from pathlib import Path

from langchain.tools import tool
from langchain_core.messages import ToolMessage

from deerflow.sandbox.patch import apply_edits, parse_patch
from deerflow.tools.types import Runtime


def _result_message(result, runtime):
    from dataclasses import asdict
    from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY, ToolResultMeta
    failed = result['status'] != 'success'
    meta = ToolResultMeta(status='partial_success' if result['status'] == 'partial' else result['status'],
                          error_type='patch_failed' if failed else None, recoverable_by_model=True,
                          recommended_next_action='try_alternative' if failed else 'continue', source='tool_return')
    return ToolMessage(content=json.dumps(result, ensure_ascii=False), name='apply_patch',
                       tool_call_id=getattr(runtime, 'tool_call_id', '') or '',
                       status='error' if failed else 'success', additional_kwargs={TOOL_META_KEY: asdict(meta)})


@tool('apply_patch', parse_docstring=True)
def apply_patch_tool(runtime: Runtime, patch: str, target: str | None = None) -> ToolMessage:
    """Create, edit, delete or move UTF-8 text files with a small exact-context patch.

    Use absolute virtual paths under /mnt/user-data/workspace/ or outputs/ in the
    selected target. Omit target for this task's default directory, use project for
    the bound directory, or an existing workspace ID to edit that worktree.
    Office custom Skill text files use /mnt/skills/custom/<skill-name>/ paths;
    they belong to the current user, independent of the selected project/worktree.
    Add never overwrites; Move refuses
    an existing destination. Context must match uniquely (no fuzzy replacements).
    Format: *** Begin Patch, one or more file sections, *** End Patch, each on
    its own line. New: *** Add File: <path> then + lines. Delete: *** Delete File:
    <path>. Edit: *** Update File: <path>, optional *** Move to: <new path>, @@,
    then context lines prefixed with space, removed lines with -, added lines
    with +. Multiple @@ hunks are allowed. @@ <exact anchor> narrows context;
    *** End of File anchors a hunk at EOF. Example:
    *** Begin Patch
    *** Update File: /mnt/user-data/workspace/example.txt
    @@
    -old text
    +new text
    *** End Patch
    Limit 80 KiB per patch and 8 MiB per UTF-8 file. Preserve existing UTF-8 BOM,
    line endings and file mode. Batch disk failures may partially apply: inspect
    the returned applied list and re-read; never blindly replay the whole patch.
    This tool edits files only; it does not publish task workspaces or run commands.

    Args:
        patch: Complete patch text in the above format (not a shell command).
        target: Optional project or existing workspace ID; selects the directory for this operation only.
    """
    from deerflow.sandbox import tools
    from deerflow.sandbox.local.local_sandbox import LocalSandbox
    try:
        edits = parse_patch(patch)
        sandbox = tools.ensure_sandbox_initialized(runtime)
        if not isinstance(sandbox, LocalSandbox):
            raise ValueError('apply_patch currently requires LocalSandbox; no remote command fallback')
        if target is None:
            tools.ensure_thread_directories_exist(runtime)
            data = tools.get_thread_data(runtime)
        else:
            from deerflow.office.execution_workspace import execution_environment, resolve_thread_data
            _, _, workspace = execution_environment(runtime.context or {}, target=target, prepare=True)
            context = {**runtime.context, 'office_execution_workspace': workspace if workspace['workspace_id'] else None}
            data = resolve_thread_data(context, runtime.state.get('thread_data'))

        def resolve(path):
            tools.validate_local_tool_path(path, data)
            actual = tools.replace_virtual_path(path, data)
            mapped = sandbox._resolve_path_with_mapping(actual)
            if sandbox._is_resolved_path_read_only(mapped):
                raise PermissionError('Read-only mount')
            target = Path(mapped.path)
            # Reject links/junctions before resolving: a move must not delete a
            # symlink target, and a parent redirect must not bypass task roots.
            if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction())
                   for p in [target, *target.parents]):
                raise PermissionError('Patch does not follow symbolic links or junctions')
            if not tools._is_custom_mount_path(path) and not tools._is_custom_skill_path(path):
                tools._validate_resolved_user_data_path(target.resolve(), data)
            return target.resolve()

        abort = (runtime.context or {}).get('abort_event')
        result = apply_edits(edits, resolve=resolve, cancelled=abort.is_set if abort else lambda: False)
        if result.get('error'):
            result['error'] = tools.mask_secret_values(tools.mask_local_paths_in_output(result['error'], data),
                                                      tools.read_active_secrets(runtime.context) or None)[:2000]
        return _result_message(result, runtime)
    except Exception as exc:
        error = tools.mask_secret_values(tools._sanitize_error(exc, runtime),
                                        tools.read_active_secrets(getattr(runtime, 'context', None)) or None)[:2000]
        return _result_message({'status': 'error', 'applied': [], 'error': error}, runtime)


async def _apply_patch_async(runtime: Runtime, patch: str, target: str | None = None) -> ToolMessage:
    from deerflow.sandbox.tools import _run_sync_tool_after_async_sandbox_init
    result = await _run_sync_tool_after_async_sandbox_init(apply_patch_tool.func, runtime, patch, target)
    return result if isinstance(result, ToolMessage) else _result_message(
        {'status': 'error', 'applied': [], 'error': result}, runtime)


apply_patch_tool.coroutine = _apply_patch_async
