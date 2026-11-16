"""Four ordinary Office tools backed by owned sessions and human approval."""
import asyncio
import json
import platform
from dataclasses import asdict
from typing import Annotated, Literal
from pydantic import Field

from langchain.tools import tool
from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY, ToolResultMeta
from deerflow.office import terminal
from deerflow.office.execution_workspace import execution_environment
from deerflow.tools.types import Runtime


def _call(runtime, name, **args):
    from deerflow.sandbox import tools
    context = runtime.context or {}
    active_secrets = tools.read_active_secrets(context) or {}
    def sanitize(value):
        return tools.mask_secret_values(str(value), active_secrets)
    try:
        target = args.pop('target', None)
        if name == 'powershell_run' and args.get('purpose') == 'preview_service' and target is None:
            target = 'project'
        service, work, workspace = execution_environment(context, target=target)
        if name == 'powershell_run':
            # Check before creating resources, and again with actual resolved cwd.
            terminal.authorize_execution(context, args['command'], args.get('cwd'))
            from deerflow.sandbox.local.local_sandbox import LocalSandbox
            if not isinstance(tools.ensure_sandbox_initialized(runtime), LocalSandbox):
                raise PermissionError('PowerShell currently supports LocalSandbox only')
            if context.get('abort_event') is None:
                raise PermissionError('A managed cancellation context is required')
            if workspace['workspace_id']:
                service.prepare(workspace['workspace_id'], work=work, context=context)
        manager = terminal.manager_for(service.store)
        wid = terminal.execution_scope(workspace)
        if name in {'powershell_read', 'powershell_stop'}:
            wid = manager.scope_for_execution(args['execution_id'], context=context)
        if name == 'powershell_run':
            result = manager.run(workspace=workspace, context=context, tool_call_id=runtime.tool_call_id, sanitize=sanitize,
                                 secret_values=active_secrets.values(), **args)
        elif name == 'powershell_read':
            result = manager.read(args['execution_id'], wid, args.get('cursor'),
                                  wait_ms=args.get('wait_ms', 30000), abort=context.get('abort_event'))
        elif name == 'powershell_stop':
            result = manager.stop(args['execution_id'], wid)
        else:
            result = {'resolved_cwd': workspace['root_path'], 'executions': manager.list(wid, include_finished=args.get('include_finished', False))}
        # Reading a failed process or intentionally stopping one is itself a
        # successful management operation. Execution state remains in content.
        failed = name == 'powershell_run' and result.get('status') in {'failed', 'timeout', 'unknown', 'cancelled', 'rejected'}
        stop = failed and result.get('status') in {'unknown', 'cancelled', 'rejected'}
        if 'execution_id' in result:
            result = terminal.tool_result(result)
    except Exception as exc:
        result, failed = {'status': 'error', 'error': sanitize(exc)[:2000]}, True
        stop = isinstance(exc, PermissionError)
    meta = ToolResultMeta(status='error' if failed else 'success', error_type='terminal_failed' if failed else None,
        recoverable_by_model=not stop, recommended_next_action='stop' if stop else 'try_alternative' if failed else 'continue', source='tool_return')
    return ToolMessage(content=json.dumps(result, ensure_ascii=False), name=name, tool_call_id=runtime.tool_call_id,
        status='error' if failed else 'success', additional_kwargs={TOOL_META_KEY: asdict(meta)},
        artifact={'terminal_execution': {'execution_id': result.get('execution_id') or args.get('execution_id'),
                                         'status': result.get('status')}}
        if result.get('execution_id') or args.get('execution_id') else None)


@tool('powershell_run', parse_docstring=True)
def powershell_run(runtime: Runtime, command: Annotated[str, Field(min_length=1, max_length=16000)], cwd: str | None = None,
                   yield_ms: Annotated[int, Field(ge=0, le=120000)] | None = None,
                   timeout_seconds: Annotated[float, Field(gt=0, le=86400)] | None = None, purpose: Literal['command', 'preview_service'] = 'command',
                   retry_of: str | None = None, target: str | None = None) -> ToolMessage:
    """Run noninteractive PowerShell; wait for ordinary commands or return a managed session.

    Build, test, install dependencies and run services. Use file tools to read or
    edit files; do not stream file contents through this terminal. No interactive
    stdin, password prompts or TUI. Ordinary commands default to the current
    environment; preview services default to the bound project. An explicit
    target overrides only this command, including worktree-local tests. Use native host paths,
    relative paths or (Get-Location).Path, not file-tool paths under /mnt/user-data
    or /mnt/office-workspaces.

    Unhandled PowerShell errors stop the script and return exit_code=1 with
    details in stderr. Native commands preserve their output and the last native
    exit code; explicit exit N can return N. Native non-zero exits do not stop
    later statements: use && for success-dependent steps or handle $LASTEXITCODE
    and explicitly exit with the intended result.
    Quote Java -D arguments: java '-Dfile.encoding=UTF-8' Main. JAVA_TOOL_OPTIONS
    supplies UTF-8 output settings; "Picked up JAVA_TOOL_OPTIONS" is informational,
    not a failure. Diagnose the actual error before changing those options.

    Created sessions return an execution_id. Running does NOT mean ready: check
    the actual HTTP response before offering a preview URL. A source dev server
    reflects edits; a build preview needs a rebuild after edits. HTTP 200 alone
    does not prove page functionality or freshness. Use URLs and route parameters
    from actual output or confirmed project routes/data, never invent them. Report the exact URL
    tested; an HTTP-only check confirms accessibility, not browser rendering or
    interactions. Untested URLs must not inherit another URL's verification.
    Bind previews to 127.0.0.1 on a port you choose and report; the client never
    receives an allocated port. Keep a working service rather than restarting it.
    For a Node server reading PORT: $env:PORT=3000; npm run dev.

    In approval mode the call blocks until the user allows or rejects the command,
    which yield_ms does not bound. Stop a test-only service before starting another
    on the same port.

    For a known failure, inspect output, fix the cause and pass retry_of referencing
    the latest failed execution. At most five retries after the first attempt.
    Reading/waiting is not retrying. Never replay unknown outcomes or user denial.
    Commands use host-user permissions; a worktree is not an OS sandbox.

    Args:
        command: PowerShell source, up to 16000 UTF-8 bytes.
        cwd: Optional relative existing directory inside the selected environment; not absolute, no drive letter, "..", symlink or .git segment.
        yield_ms: Wait 0–120000 milliseconds; default 30000 for commands, 1000 for preview services. Use 120000 for builds and installs instead of repeated reads.
        timeout_seconds: Execution deadline, up to 86400 seconds; defaults 600, or 3600 for preview services.
        purpose: Fixed execution type, not a free-text reason. command is cleaned up at task end. preview_service survives normal task end, but stops on its execution deadline, cancellation, explicit stop or Gateway shutdown/restart.
        retry_of: Latest failed/timed-out execution ID in the same environment, only when retrying.
        target: Omit for the current environment on command, or the bound project on preview_service. project selects the bound directory; an existing workspace ID explicitly selects that worktree.
    """
    return _call(runtime, 'powershell_run', command=command, cwd=cwd, yield_ms=yield_ms,
                 timeout_seconds=timeout_seconds, purpose=purpose, retry_of=retry_of, target=target)


@tool('powershell_read', parse_docstring=True)
def powershell_read(runtime: Runtime, execution_id: str, cursor: str | None = None,
                    wait_ms: Annotated[int, Field(ge=0, le=120000)] = 30000) -> ToolMessage:
    """Read an owned process's real state and incremental bounded stdout/stderr.

    Use output_cursor from the preceding result to avoid repeated output. Each
    stream returns at most 16000 characters per call and retains its first 256 KiB
    with a truncation marker. When a terminal result includes output_tail, check
    that final excerpt instead of paging a long log to find its ending.

    Determine the outcome from status and exit_code, not output wording. Unknown
    after a Gateway restart requires inspection, not blind replay. Historical
    startup output does not prove an unknown or stopped preview is available.
    When running,
    wait for completion rather than polling each log line.
    Returns on completion, cancellation or the wait deadline; wait_ms=0 reads
    immediately. Finished executions stay readable. Waiting never restarts the
    command or consumes a retry.

    Args:
        execution_id: Office execution ID returned by powershell_run or found with powershell_list, not a host PID or port. May belong to an earlier task or another employee in the same user/project scope.
        cursor: Optional opaque output_cursor from the preceding read/run result.
        wait_ms: Wait 0–120000 milliseconds for completion; default 30000.
    """
    return _call(runtime, 'powershell_read', execution_id=execution_id, cursor=cursor, wait_ms=wait_ms)


@tool('powershell_list', parse_docstring=True)
def powershell_list(runtime: Runtime, target: str | None = None, include_finished: bool = False) -> ToolMessage:
    """List execution IDs, states, command summaries and working directories in an environment.

    Use before starting another service or when you lost an execution ID. This
    never lists arbitrary host processes or another user's/project's executions.
    resolved_cwd identifies the queried directory, even when executions is empty;
    it does not include services in other worktrees of the project.
    When restoring a preview, inspect current sessions and relevant output rather
    than assuming a historical URL is still live.
    By default returns active and unknown executions only. Known execution IDs
    remain readable regardless of this list filter.

    Args:
        target: Omit for the current environment; project selects the bound directory, or supply an existing workspace ID.
        include_finished: Include completed, failed, rejected and stopped executions when historical records are needed. Default False.
    """
    return _call(runtime, 'powershell_list', target=target, include_finished=include_finished)


@tool('powershell_stop', parse_docstring=True)
def powershell_stop(runtime: Runtime, execution_id: str) -> ToolMessage:
    """Stop an owned terminal process tree and return its actual final state.

    Repeating stop on an already finished execution is safe. It accepts an owned
    execution ID only, never a host PID or port. A successfully stopped service
    releases its listening port. Stopping does not undo file edits.

    Args:
        execution_id: Office execution ID returned by powershell_run or found with powershell_list, not a host PID or port. May belong to an earlier task or another employee in the same user/project scope.
    """
    return _call(runtime, 'powershell_stop', execution_id=execution_id)


def _async_adapter(fn):
    async def invoke(runtime, **kwargs):
        pending = asyncio.create_task(asyncio.to_thread(fn, runtime, **kwargs))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            abort = (runtime.context or {}).get('abort_event')
            if abort:
                abort.set()
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
            raise
    return invoke


_host = platform.system()
_host_description = {
    'Darwin': 'Current host: macOS; shell: PowerShell 7. Use macOS command names and paths, '
              'such as curl and .venv/bin/python; do not use curl.exe, NUL or Windows drive paths. '
              'Discard output with Out-Null or $null.',
    'Windows': 'Current host: Windows; shell: PowerShell 7. Use Windows command names and paths, '
               'such as curl.exe and .venv/Scripts/python.exe. Discard output with Out-Null or $null.',
}.get(_host, f'Current host: {_host}; shell: PowerShell 7. Use native host command names and paths.')
powershell_run.description = _host_description + '\n\n' + powershell_run.description

for _tool in (powershell_run, powershell_read, powershell_list, powershell_stop):
    _tool.coroutine = _async_adapter(_tool.func)
