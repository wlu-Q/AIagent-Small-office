"""Isolated HTTP Gateway for desktop integration; only the model is controlled.

Run with: python tests/office_gateway_fixture.py <empty-data-directory> <port>
The --reuse flag reopens only a directory initialized by this test fixture.
No production model keys or user project files are read.
"""
from patch_helpers import add_patch, update_patch

import asyncio
import html
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def goal_reply(text, results):
    """Deterministic choices only; files, delegation, checkpoints and gates are real."""
    import re
    matches = list(re.finditer(r'GOAL_(DIRECT|BUILD|DOC|REVIEW|REWORK|ASK|MULTI|TODO|KNOWLEDGE_BAD|KNOWLEDGE|WORKER)\b', text))
    if not matches:
        return None
    scenario = 'WORKER' if 'GOAL_WORKER TARGET=' in text else matches[-1][1]
    def call(name, **args):
        return AIMessage(content='', tool_calls=[dict(name=name, args=args, id=f'goal-{scenario}-{len(results)}')])
    names = [m.name for m in results]
    if scenario == 'TODO':
        if not results:
            return call('write_todos', todos=[{'content': 'Blocked remaining work', 'status': 'pending'}])
        return AIMessage(content='GOAL_TODO_DONE' if results[-1].status != 'error' else 'TODO_TOOL_FAILED')
    if scenario in {'KNOWLEDGE', 'KNOWLEDGE_BAD'}:
        if not results:
            source = re.search(r'message_id=([^\]\s]+)', text)
            if scenario == 'KNOWLEDGE_BAD':
                return call('capture_user_message', message_id='not-a-real-source', category='constraint', topic='department-filter')
            if source is None:
                return AIMessage(content='KNOWLEDGE_SOURCE_ID_MISSING')
            return call('capture_user_message', message_id=source[1], category='constraint', topic='department-filter')
        return AIMessage(content=f'GOAL_{scenario}_DONE: ' + str(results[-1].content))
    if scenario == 'MULTI' and names.count('ask_clarification') < 2:
        return call('ask_clarification', question='Department?' if not results else 'Color?', clarification_type='missing_info')
    if scenario == 'WORKER':
        target = re.search(r'TARGET=(\S+)', text)[1]
        if not results:
            return call('read_file', description='Inspect current file', path=f'/mnt/user-data/workspace/{target}')
        if len(results) == 1:
            content = 'department=engineering\n' if target == 'filter.txt' else '# Product introduction\nPM coordinates employees.\n'
            return call('apply_patch', patch=(add_patch if results[-1].status == 'error' else lambda p, c: update_patch(p, str(results[-1].content), c))(f'/mnt/user-data/workspace/{target}', content))
        return AIMessage(content='Employee completed requested file; tool result: ' + str(results[-1].content))
    if scenario == 'ASK' and 'ask_clarification' not in names:
        return call('ask_clarification', question='Which department?', clarification_type='missing_info')
    if scenario in {'DIRECT', 'BUILD', 'REVIEW', 'REWORK'} and not results:
        return call('read_file', description='Analyze input', path='/mnt/user-data/workspace/README.md')
    if scenario in {'DIRECT', 'REVIEW'}:
        return AIMessage(content=f'GOAL_{scenario}_DONE: ' + str(results[-1].content))
    if scenario == 'REWORK' and len(results) == 1:
        return call('task', subagent_type='missing-employee', description='Unavailable employee', prompt='Do work')
    if 'task' not in names or (scenario == 'REWORK' and names.count('task') == 1):
        target = 'intro.md' if scenario == 'DOC' else 'filter.txt'
        answer = str(results[-1].content) if scenario == 'ASK' else ''
        return call('task', subagent_type='writer', description='Deliver requested file',
                    prompt=f'GOAL_WORKER TARGET={target} {answer}')
    if names[-1] == 'task':
        target = 'intro.md' if scenario == 'DOC' else 'filter.txt'
        workspace = re.search(r'/mnt/office-workspaces/[a-f0-9]+', str(results[-1].content))
        prefix = workspace[0] if workspace else '/mnt/user-data'
        return call('read_file', description='Verify employee output', path=f'{prefix}/workspace/{target}')
    return AIMessage(content=f'GOAL_{scenario}_DONE: ' + str(results[-1].content))


def terminal_reply(text, results, messages):
    if 'TERMINAL_CHILD' not in text and 'E2E_TERMINAL_DIRECT' not in text:
        if not results:
            return AIMessage(content='', tool_calls=[dict(id='terminal-child', name='task', args=dict(
                subagent_type='writer', description='验证终端后台执行', prompt='TERMINAL_CHILD 请执行终端验收并给我文件'))])
        return AIMessage(content='E2E_TERMINAL_PM_DONE ' + str(results[-1].content))
    def call(name, **args):
        return AIMessage(content='', tool_calls=[dict(id=f'terminal-{len(results)}', name=name, args=args)])
    if not results:
        return call('powershell_run', command="Write-Output 'background'; Start-Sleep 60", yield_ms=10)
    original = json.loads(results[0].content)
    if results[0].status == 'error':
        return AIMessage(content='TERMINAL_FAILED ' + str(results[0].content))
    if len(results) == 1:
        return call('powershell_run', command="Set-Content -LiteralPath 'shell-result.txt' -Value '终端任务完成' -Encoding utf8", yield_ms=10000)
    if len(results) == 2:
        return call('powershell_list')
    if len(results) == 3:
        return call('powershell_stop', execution_id=original['execution_id'])
    if len(results) == 4:
        return call('powershell_read', execution_id=original['execution_id'])
    if len(results) == 5:
        return call('read_file', description='核对真实命令产物', path='/mnt/user-data/workspace/shell-result.txt')
    scoped = re.search(r'/mnt/office-workspaces/[a-f0-9]+', '\n'.join(str(m.content) for m in messages))
    path = (scoped[0] if scoped else '/mnt/user-data') + '/workspace/shell-result.txt'
    return AIMessage(content=f'E2E_TERMINAL_DONE 已在绑定目录完成。\n[shell-result.txt]({path})\n' + str(results[-1].content))


def delivery_reply(text, results):
    def call(name, **args):
        return AIMessage(content='', tool_calls=[dict(id=f'delivery-{len(results)}', name=name, args=args)])
    if 'E2E_DELIVERY_PM' in text:
        if not results:
            return AIMessage(content='', tool_calls=[dict(id=f'part-{label}', name='task', args=dict(
                subagent_type=employee, description=f'独立施工 {label}', prompt=f'DELIVERY_CHILD_{label}', workspace_mode='new'))
                for label, employee in [('A', 'writer'), ('B', 'writer2')]])
        if len(results) == 2:
            refs = [json.loads(str(t.content).split('Task workspace facts: ')[-1])['workspace_id'] for t in results]
            return call('task', subagent_type='writer', description='集成两位员工的成果',
                        prompt=f'DELIVERY_CHILD_INTEGRATE SOURCE={refs[1]} 保留A和B的需求，解决冲突后交付',
                        workspace_mode='continue', workspace_id=refs[0])
        return AIMessage(content='E2E_DELIVERY_PM_DONE ' + str(results[-1].content))
    if 'E2E_DELIVERY_TERMINAL' in text:
        if not results:
            return call('task', subagent_type='writer', description='验证批准与交付', prompt='DELIVERY_CHILD_TERMINAL', workspace_mode='new')
        return AIMessage(content='E2E_DELIVERY_TERMINAL_DONE ' + str(results[-1].content))
    if 'E2E_DELIVERY_REJECT' in text:
        if not results: return call('powershell_run', command="Set-Content -LiteralPath rejected.txt -Value no", yield_ms=10000)
        return AIMessage(content='E2E_DELIVERY_REJECT_DONE ' + str(results[-1].content))
    if 'DELIVERY_CHILD_TERMINAL' in text:
        if not results: return call('powershell_run', command="Set-Content -LiteralPath README.md -Value '# TERMINAL_DELIVERED' -Encoding utf8", yield_ms=10000)
        if len(results) == 1: return call('powershell_run', command="if ((Get-Content README.md -Raw) -notmatch 'TERMINAL_DELIVERED') { throw 'wrong content' }; Write-Output verified", yield_ms=10000)
        if len(results) == 2: return call('publish_task_result')
        return AIMessage(content='DELIVERY_CHILD_DONE ' + str(results[-1].content))
    path = '/mnt/user-data/workspace/README.md'
    if 'DELIVERY_CHILD_INTEGRATE' in text:
        if not results:
            return call('publish_task_result', source_workspace_ids=[re.search(r'SOURCE=([a-f0-9]+)', text)[1]])
        if len(results) == 1:
            return call('read_file', description='读取实际合并冲突', path=path)
        if len(results) == 2:
            return call('apply_patch', patch=update_patch(path, str(results[-1].content), '# A + B\n'))
        if len(results) == 3:
            return call('powershell_run', command='git add -- README.md', yield_ms=10000)
        if len(results) == 4:
            return call('publish_task_result')
    else:
        label = 'A' if 'DELIVERY_CHILD_A' in text else 'B'
        if not results:
            return call('read_file', description='读取基线', path=path)
        if len(results) == 1:
            return call('apply_patch', patch=update_patch(path, str(results[-1].content), f'# {label}\n'))
    return AIMessage(content='DELIVERY_CHILD_DONE ' + str(results[-1].content))


class DesktopTestModel(BaseChatModel):
    model: str = 'desktop-controlled'
    slow_seen: set[str] = set()
    bound_tools: list[str] = []

    @property
    def _llm_type(self):
        return 'desktop-integration-controlled'

    def bind_tools(self, tools, **kwargs):
        names = [t.get('name') if isinstance(t, dict) else t.name for t in tools]
        return self.model_copy(update={'bound_tools': names})

    def _generate(self, messages, **kwargs):
        last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
        raw_text = str(messages[last_human].content)
        text = raw_text if 'GOAL_WORKER TARGET=' in raw_text else re.split(r'\[当前 @PM 请求[^\]]*\]', raw_text)[-1]
        contract_text = text
        work_data = re.search(r'<work_data>(.*?)</work_data>', html.unescape(raw_text), flags=re.S)
        if work_data:
            contract_text = json.loads(work_data[1])['task']
        tools = [m for m in messages[last_human + 1:] if isinstance(m, ToolMessage)]
        if 'E2E_ROUND_TIMEOUT' in text or ('ROUND_PARTIAL_WORKER' in contract_text and len(tools) >= 2):
            from deerflow.agents.middlewares.office_round_budget import _ACTIVITY
            clock = _ACTIVITY.get()
            if clock is None:
                raise RuntimeError('Office round timing middleware is missing')
            with clock.lock:
                clock.consumed += 901
            clock.check()
        if 'E2E_ROUND_PARTIAL' in text or 'ROUND_PARTIAL_WORKER' in contract_text or 'ROUND_CONTINUE_WORKER' in contract_text:
            def call(name, **args):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[{
                    'name': name, 'args': args, 'id': f'round-{len(tools)}'}]))])
            if 'ROUND_PARTIAL_WORKER' not in contract_text and 'ROUND_CONTINUE_WORKER' not in contract_text:
                if not tools:
                    return call('task', subagent_type='writer', description='保留部分成果', prompt='ROUND_PARTIAL_WORKER 修改README', workspace_mode='new')
                if len(tools) == 1:
                    facts = tools[-1].additional_kwargs['office_execution_workspace']
                    return call('task', subagent_type='writer2', description='接续部分成果',
                                prompt='ROUND_CONTINUE_WORKER 保留原有内容，补充剩余内容', workspace_mode='continue', workspace_id=facts['workspace_id'])
                reply = AIMessage(content='E2E_ROUND_PARTIAL_DONE ' + str(tools[-1].content))
            else:
                filename = '/mnt/user-data/workspace/README.md'
                if not tools:
                    return call('read_file', description='读取接续基线', path=filename)
                if len(tools) == 1:
                    prior = str(tools[-1].content)
                    if 'ROUND_CONTINUE_WORKER' in contract_text and 'PARTIAL_BASE' not in prior:
                        raise RuntimeError('Partial work was lost before continuation')
                    content = '# PARTIAL_BASE\n' if 'ROUND_PARTIAL_WORKER' in contract_text else '# PARTIAL_BASE\nCONTINUED\n'
                    return call('apply_patch', patch=update_patch(filename, prior, content))
                reply = AIMessage(content='CONTINUED_FROM_PARTIAL ' + str(tools[-1].content))
            return ChatResult(generations=[ChatGeneration(message=reply)])
        if 'E2E_DELIVERY' in text or 'DELIVERY_CHILD_' in contract_text:
            return ChatResult(generations=[ChatGeneration(message=delivery_reply(contract_text, tools))])
        if 'E2E_TERMINAL' in text or 'TERMINAL_CHILD' in contract_text:
            return ChatResult(generations=[ChatGeneration(message=terminal_reply(contract_text, tools, messages))])
        # Keep the full PM envelope for controlled goal scenarios: the current
        # request marker carries the stable source message ID used by the
        # restricted project-knowledge tool.
        goal = goal_reply(raw_text, tools)
        if 'WORKTREE_CHILD' in contract_text or 'E2E_WORKTREE_DIRECT' in text:
            label = 'DIRECT' if 'E2E_WORKTREE_DIRECT' in text else re.search(r'WORKTREE_CHILD ([AB])', contract_text)[1]
            path = '/mnt/user-data/workspace/README.md'
            if not tools:
                reply = AIMessage(id='shared-step-start' if label != 'DIRECT' else None, content=[
                    {'type': 'reasoning', 'text': 'PRIVATE_EMPLOYEE_REASONING'},
                    {'type': 'text', 'text': '我先读取当前项目，再完成这一版说明。'},
                ], tool_calls=[{'id': 'tree-read', 'name': 'read_file', 'args': {'description': 'Read baseline', 'path': path}}])
            elif len(tools) == 1:
                reply = AIMessage(content='', tool_calls=[{'id': 'tree-write', 'name': 'apply_patch', 'args': {'patch': update_patch(path, str(tools[-1].content), f'# WORKTREE_{label}\n')}}])
            elif label == 'DIRECT' and len(tools) == 2:
                reply = AIMessage(content='', tool_calls=[{'id': 'tree-ask', 'name': 'ask_clarification', 'args': {
                    'question': '任务目录里的修改已保存，还要继续核对吗？', 'clarification_type': 'missing_info'}}])
            elif tools[-1].name in {'apply_patch', 'ask_clarification'}:
                reply = AIMessage(content='', tool_calls=[{'id': 'tree-verify', 'name': 'read_file', 'args': {'description': 'Verify task file', 'path': path}}])
            else:
                scoped = re.search(r'/mnt/office-workspaces/[a-f0-9]+', '\n'.join(str(m.content) for m in messages[:last_human]))
                link = scoped[0] + '/workspace/README.md' if scoped else path
                reply = AIMessage(content=f'WORKTREE_{label}_DONE：独立目录已修改，尚未发布。\n[README.md]({link})\n' + str(tools[-1].content))
        elif 'E2E_WORKTREE' in text:
            if not tools:
                reply = AIMessage(content='我会让两位员工分别准备一版说明，再核对两份结果。', tool_calls=[{'id': f'tree-{label}', 'name': 'task', 'args': {
                    'subagent_type': employee, 'workspace_mode': 'new', 'description': f'独立修改方案 {label}', 'prompt': f'WORKTREE_CHILD {label} 修改README'}}
                    for label, employee in [('A', 'writer'), ('B', 'writer2')]])
            elif all(t.name == 'task' for t in tools):
                refs = [re.search(r'/mnt/office-workspaces/[a-f0-9]+', str(t.content))[0] + '/workspace/README.md' for t in tools]
                reply = AIMessage(content='两份修改都回来了，我检查一下实际文件。', tool_calls=[{'id': f'check-{i}', 'name': 'read_file', 'args': {
                    'description': 'Inspect actual employee result', 'path': ref}} for i, ref in enumerate(refs)])
            else:
                refs = [re.search(r'/mnt/office-workspaces/[a-f0-9]+', str(t.content))[0] for t in tools if t.name == 'task']
                reply = AIMessage(content='E2E_WORKTREE_DONE：两份独立方案已核对，尚未发布。\n' + '\n'.join(
                    f'[方案{i+1}]({ref}/workspace/README.md)' for i, ref in enumerate(refs)) + '\n' + '\n'.join(str(t.content) for t in tools if t.name == 'read_file'))
        elif 'LIMIT_CHILD' in contract_text:
            reply = AIMessage(content='LIMIT_CHILD_DONE ' + json.dumps(sorted(self.bound_tools)))
        elif 'E2E_LIMIT_PARALLEL' in text:
            completed = len([m for m in tools if m.name == 'task'])
            if completed == 0:
                slots = range(6)  # Deliberately exceed the per-response cap.
            elif completed == 5:
                slots = [5]  # Explicitly resubmit the rejected call next turn.
            else:
                slots = []
            reply = AIMessage(content='' if slots else 'E2E_LIMIT_PARALLEL_DONE', tool_calls=[
                {'name': 'task', 'id': f'parallel-{completed}-{i}', 'args': {
                    'subagent_type': 'writer' if i == 0 else f'writer{i+1}',
                    'description': f'独立分析 {i+1}', 'prompt': f'LIMIT_CHILD WAIT SLOT={i}'}}
                for i in slots])
        elif 'E2E_LIMIT_TOTAL' in text:
            completed = len([m for m in tools if m.name == 'task'])
            slots = range(5) if completed < 20 else range(1)
            reply = AIMessage(content='', tool_calls=[
                {'name': 'task', 'id': f'total-{completed}-{i}', 'args': {
                    'subagent_type': 'writer' if i == 0 else f'writer{i+1}',
                    'description': f'分析条目 {completed+i+1}', 'prompt': f'LIMIT_CHILD ITEM={completed+i+1}'}}
                for i in slots])
        elif 'E2E_VISUALIZATION' in text:
            if not tools:
                location = re.search(r'Location: (.+/office-visualization/SKILL.md)', str(messages[0].content))
                if not location:
                    raise AssertionError('PM cannot discover office-visualization')
                reply = AIMessage(content='', tool_calls=[{'name': 'read_file', 'id': 'visual-read',
                    'args': {'description': '读取对话图表规范', 'path': html.unescape(location[1].strip())}}])
            else:
                if '对话图表' not in str(tools[-1].content):
                    raise AssertionError('PM cannot read the visualization skill')
                content = (Path(os.environ['DEER_FLOW_HOME']) / 'project/diagrams.md').read_text(encoding='utf-8')
                reply = AIMessage(content=content + '\n\n[图表说明](diagrams.md)')
        elif 'E2E_FILE_PREVIEW' in text:
            reply = AIMessage(content=(
                '文件已整理好，可以点开看看。\n\n'
                '我调整了 [样式文件](components.css)，没有改动其他模块。\n\n'
                '随后可以查看 [尚未创建的文件](deleted.txt)，不存在就应显示真实错误。\n\n'
                f'[预览地址](http://127.0.0.1:{sys.argv[2]}/test-preview/site)\n\n'
                '[README.md](README.md) [components.css](components.css) '
                '[preview.png](preview.png) [index.html](index.html) '
                '[不支持的文档](report.docx) [已删除文件](deleted.txt)'
            ))
        elif 'CONTRACT_BOUND' in contract_text:
            reply = AIMessage(content='CONTRACT_BOUND ' + json.dumps(sorted(self.bound_tools)))
        elif 'CONTRACT_DELEGATE' in text and not tools:
            reply = AIMessage(content='', tool_calls=[{'name': 'task', 'id': 'contract-child',
                'args': {'subagent_type': 'writer', 'description': 'Check employee tools', 'prompt': 'CONTRACT_BOUND'}}])
        elif 'CONTRACT_DELEGATE' in text:
            reply = AIMessage(content='CONTRACT_RESULT ' + str(tools[-1].content))
        elif goal is not None:
            reply = goal
        elif 'E2E_EMPLOYEE' in contract_text:
            reply = AIMessage(content='员工产品介绍：小小办公室由 PM 协调员工完成用户目标。')
        elif 'E2E_ASK' in text and not tools:
            reply = AIMessage(content='', tool_calls=[{'name': 'ask_clarification', 'id': 'ask-e2e',
                'args': {'question': '请选择筛选部门', 'clarification_type': 'missing_info',
                         **({'options': ['产品部门', '研发部门']} if 'E2E_ASK_AFTER_PAUSE' in text else {})}}])
        elif 'E2E_DELEGATE' in text and not tools:
            reply = AIMessage(content='', tool_calls=[{'name': 'task', 'id': 'delegate-e2e',
                'args': {'subagent_type': 'writer', 'description': '编写产品介绍', 'prompt': 'E2E_EMPLOYEE 编写简短产品介绍'}}])
        elif 'E2E_ASK' in text:
            reply = AIMessage(content='E2E_ASK_DONE：已根据用户回答完成部门筛选分析。')
        elif 'E2E_DELEGATE' in text:
            reply = AIMessage(content='E2E_DELEGATE_DONE：已收到员工产品介绍并完成整理。')
        elif 'E2E_SLOW' in text:
            reply = AIMessage(content='E2E_SLOW_DONE：暂停后继续完成。')
        else:
            reply = AIMessage(content='E2E_DIRECT_DONE：当前前端采用 React，办公室画布使用 Phaser。')
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def _agenerate(self, messages, **kwargs):
        text = str(next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ''))
        last_human = max((index for index, message in enumerate(messages) if isinstance(message, HumanMessage)), default=0)
        if 'WORKTREE_CHILD' in text and sum(isinstance(message, ToolMessage) for message in messages[last_human + 1:]) == 1:
            await asyncio.sleep(8)
        if 'LIMIT_CHILD WAIT' in text:
            await asyncio.sleep(4)
        if 'E2E_SLOW' in text and text not in self.slow_seen:
            self.slow_seen.add(text)
            await asyncio.sleep(8)
        return self._generate(messages, **kwargs)


def main():
    import yaml

    backend = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(backend), str(backend / 'packages/harness')]
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    reuse = sys.argv[3:] == ['--reuse']
    marker = root / '.office-gateway-test-fixture'
    if reuse and (not marker.is_file() or marker.read_text(encoding='utf-8') != str(root)):
        raise ValueError('Refusing to reuse a directory not owned by this test fixture')
    if not reuse and any(root.iterdir()):
        raise ValueError('Desktop integration requires an empty data directory')
    config_path = root / 'config.yaml'
    os.environ.update(DEER_FLOW_HOME=str(root), DEER_FLOW_CONFIG_PATH=str(config_path),
                      DEER_FLOW_AUTH_DISABLED='true', SMALL_OFFICE_USER_ID='default')
    project = root / 'project'
    if not reuse:
        config_path.write_text(yaml.safe_dump({
            'models': [{'name': 'office-default', 'model': 'desktop-controlled',
                        'supports_vision': os.environ.get('OFFICE_TEST_TOOL_CONTRACT') == '1',
                        'use': 'office_gateway_fixture:DesktopTestModel'}],
            'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'},
            'skills': {'path': str(Path(__file__).resolve().parents[2] / 'skills')},
            'tools': [
                {'name': 'read_file', 'use': 'deerflow.sandbox.tools:read_file_tool', 'group': 'file:read'},
                {'name': 'apply_patch', 'use': 'deerflow.sandbox.patch_tool:apply_patch_tool', 'group': 'file:write'},
            ] + ([{'name': name, 'use': f'deerflow.sandbox.powershell_tools:{name}',
                    'group': 'powershell_terminal', 'permissions': ['process.execute']}
                   for name in ('powershell_run', 'powershell_read', 'powershell_list', 'powershell_stop')]
                  if os.environ.get('OFFICE_TEST_TERMINAL') == '1' else []),
            'database': {'backend': 'sqlite', 'sqlite_dir': str(root / 'data')},
            'run_events': {'backend': 'db'},
            'memory': {'enabled': os.environ.get('OFFICE_TEST_TOOL_CONTRACT') == '1', 'mode': 'tool', 'injection_enabled': False}, 'title': {'enabled': False},
            'summarization': {'enabled': False}, 'tool_search': {'enabled': False},
        }), encoding='utf-8')
        settings = root / 'users/default/office-bootstrap.yaml'
        settings.parent.mkdir(parents=True, exist_ok=True)
        if os.environ.get('OFFICE_TEST_BOOTSTRAP') != '1':
            settings.write_text(yaml.safe_dump({'version': 1, 'company_name': '第四阶段联调',
                'default_model_profile_id': 'office-default', 'thinking_level': 'off'}), encoding='utf-8')
        project.mkdir(exist_ok=True)
        (project / 'README.md').write_text('# 临时联调项目\n', encoding='utf-8')
        from deerflow.office.execution_workspace import _git
        _git(project, 'init')
        _git(project, 'add', '.')
        _git(project, 'commit', '-m', 'Integration fixture baseline')
        marker.write_text(str(root), encoding='utf-8')
    from app.gateway.app import app
    original = app.router.lifespan_context
    preview_visits = []

    @app.get('/test-workspaces')
    def workspace_facts():
        from deerflow.config.paths import get_paths
        from deerflow.office.execution_facts import ExecutionReceiptStore
        from deerflow.office.execution_workspace import _git
        store = ExecutionReceiptStore(paths=get_paths(), user_id='default')
        with store._connect() as db:
            values = [json.loads(row[0]) for row in db.execute('SELECT state_json FROM execution_workspaces')]
            receipts = [dict(row) for row in db.execute('SELECT receipt_key,employee_id,status,result_ref_json FROM delegation_receipts')]
        return {'workspaces': values, 'source': (project / 'README.md').read_text(encoding='utf-8'),
                'status': _git(project, 'status', '--porcelain').decode(), 'receipts': receipts}

    @app.get('/test-preview/site')
    def test_preview_site():
        from fastapi.responses import HTMLResponse
        preview_visits.append(True)
        return HTMLResponse('<meta charset="utf-8"><title>办公室预览测试</title><h1>浏览器预览已打开</h1><p>这是一张本地测试页面，可以关闭。</p>')

    @app.get('/test-preview/visits')
    def test_preview_visits():
        return {'count': len(preview_visits)}

    # Test-only OpenAI-compatible endpoint exercises the real HTTP adapter,
    # including tool call serialization and streaming, without vendor keys.
    from fastapi import Request
    from fastapi.responses import StreamingResponse
    from langchain_openai.chat_models.base import _convert_dict_to_message, _convert_message_to_dict

    model_requests = []

    @app.get('/test-model/requests')
    async def request_parameters():
        return model_requests

    @app.post('/test-model/v1/chat/completions')
    async def controlled_completion(request: Request):
        body = await request.json()
        model_requests.append({'model': body['model'], 'reasoningEffort': body.get('reasoning_effort'),
            'thinking': body.get('thinking'), 'stream': bool(body.get('stream')),
            'tools': [t.get('function', {}).get('name') for t in body.get('tools', [])]})
        messages = body['messages']
        if any(t.get('function', {}).get('name') == 'office_connection_probe' for t in body.get('tools', [])):
            if messages[-1]['role'] == 'tool':
                message = {'role': 'assistant', 'content': messages[-1]['content']}
            else:
                message = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'probe', 'type': 'function',
                    'function': {'name': 'office_connection_probe', 'arguments': '{}'}}]}
        else:
            reply = DesktopTestModel(model=body['model'])._generate([_convert_dict_to_message(m) for m in messages])
            message = _convert_message_to_dict(reply.generations[0].message)
        finish = 'tool_calls' if message.get('tool_calls') else 'stop'
        common = {'id': 'controlled-http', 'created': 1, 'model': body['model']}
        if body.get('stream'):
            delta = dict(message)
            if delta.get('tool_calls'):
                delta['tool_calls'] = [{**t, 'index': i} for i, t in enumerate(delta['tool_calls'])]
            async def chunks():
                for data in [
                    {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {**common, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
                ]:
                    yield 'data: ' + json.dumps(data) + '\n\n'
                yield 'data: [DONE]\n\n'
            return StreamingResponse(chunks(), media_type='text/event-stream')
        return {**common, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}

    @app.get('/test-model/contracts')
    async def model_contracts():
        records = await app.state.run_manager.list_by_thread('office__default__pm__group', user_id='default')
        return [{'runId': r.run_id, 'modelId': r.kwargs.get('launch_contract', {}).get('model_profile_id'),
                 'resumeSource': (r.metadata or {}).get('office_resume_source_run_id')} for r in records]

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            employees = application.state.office_employee_service
            await employees.ensure_builtin_pm(default_model_profile_id='office-default')
            if not any(e['id'] == 'writer' for e in await employees.list_employees()):
                await employees.create_employee({'id': 'writer', 'templateId': 'research-documentation',
                    'displayName': '文档员工', 'avatar': 'ash', 'modelProfileId': 'office-default'})
            extra_workers = 0
            if any(os.environ.get(key) == '1' for key in ('OFFICE_TEST_WORKSPACES', 'OFFICE_TEST_DELIVERY')):
                extra_workers = 1
            if os.environ.get('OFFICE_TEST_DELEGATION_LIMITS') == '1':
                extra_workers = 5
            if extra_workers:
                existing = {e['id'] for e in await employees.list_employees()}
                for i in range(2, 2 + extra_workers):
                    if f'writer{i}' not in existing:
                        await employees.create_employee({'id': f'writer{i}', 'templateId': 'research-documentation',
                            'displayName': f'文档员工{i}', 'avatar': 'ash', 'modelProfileId': 'office-default'})
            if not reuse and os.environ.get('OFFICE_TEST_BOOTSTRAP') != '1':
                application.state.office_project_service.bind(project)
            if os.environ.get('OFFICE_TEST_DELIVERY') == '1':
                current = employees.get_employee_tools('writer')
                if 'powershell_terminal' not in current['tools']:
                    await employees.save_employee_tools('writer', {'tools': current['tools'] + ['powershell_terminal'], 'expectedRevision': current['revision']})
            if os.environ.get('OFFICE_TEST_TERMINAL') == '1':
                # Isolated fixture only: production has no context/env bypass.
                from deerflow.office import terminal
                from deerflow.office.tool_manifest import EmployeeToolManifest, load_tool_manifest, save_tool_manifest, manifest_revision
                terminal.authorize_execution = lambda *args: 'full_access'
                agent_dir = root / 'users/default/agents/writer'
                manifest = load_tool_manifest(agent_dir)
                save_tool_manifest(agent_dir, EmployeeToolManifest(1, tuple(sorted(set(manifest.tools) |
                    {'powershell_run', 'powershell_read', 'powershell_list', 'powershell_stop'}))), expected_revision=manifest_revision(manifest))
            yield
    app.router.lifespan_context = lifespan
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), loop='asyncio:SelectorEventLoop')


if __name__ == '__main__':
    main()
