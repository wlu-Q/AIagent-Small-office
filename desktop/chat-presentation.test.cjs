const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../client/node_modules/typescript')

const filename = path.join(__dirname, '../client/src/services/office/officeEvents.ts')
const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText
const exported = {}
vm.runInNewContext(output, { exports: exported, require: name => name.endsWith('/i18n') ? require('./i18n-test-helper.cjs') : ({ officeApi: {} }) }, { filename })
const { projectOfficeRunEvents: project, mergeVisibleRunEvents: merge } = exported
const event = (event_type, content, seq = 1, metadata = {}) => ({
  run_id: 'run-a', thread_id: 'thread-a', event_type, content, seq, metadata,
  created_at: '2026-09-09T01:00:00Z', category: 'message',
})

test('employee tool steps expose producer status and original call identity', () => {
  for (const status of ['success', 'error']) {
    const visible = project([event('subagent.step', {
      kind: 'tool', employee_id: 'frontend', task_id: 'task-one', tool_name: 'read_file',
      tool_call_id: 'call-one', status, text: 'Error: document body is not a status signal',
    })])
    assert.equal(visible[0].callId, 'call-one')
    assert.equal(visible[0].status, status === 'error' ? 'error' : 'done')
    assert.ok(!visible[0].detail.includes('document body'))
  }
})

test('delegated tools render once with employee status across polling batches', () => {
  const result = (name, id, seq, caller) => project([
    event('llm.tool.result', { name, tool_call_id: id, status: 'success' }, seq, caller ? { caller } : {}),
  ])
  const step = (name, id, employee, task, seq, status) => project([event('subagent.step', {
    kind: 'tool', employee_id: employee, task_id: task, tool_name: name, tool_call_id: id, status,
  }, seq, { employee_id: employee, task_id: task })])
  const read = result('read_file', 'read', 11)
  const failed = result('ls', 'failed', 18, 'subagent:frontend')
  assert.equal(read[0].caller, undefined)
  assert.equal(failed[0].employeeId, 'frontend')
  const firstPoll = merge([...read, ...failed])
  const steps = [
    ...step('read_file', 'read', 'frontend', 'task-a', 15, 'success'),
    ...step('ls', 'failed', 'frontend', 'task-a', 23, 'error'),
    ...step('ls', 'parallel', 'frontend-b', 'task-b', 24, 'success'),
  ]
  const pmCall = project([event('llm.ai.response', { tool_calls: [{ name: 'ls', id: 'pm-ls', args: {} }] }, 26)])
  const pmResult = result('ls', 'pm-ls', 27)
  for (const items of [[...firstPoll, ...steps], [...steps, ...firstPoll]]) {
    const visible = merge([...items, ...pmCall, ...pmResult])
    assert.equal(visible.length, 4)
    assert.equal(visible.find(item => item.callId === 'read').employeeId, 'frontend')
    assert.equal(visible.find(item => item.callId === 'failed').status, 'error')
    assert.equal(visible.find(item => item.callId === 'parallel').employeeId, 'frontend-b')
    assert.equal(visible.find(item => item.callId === 'pm-ls').caller, 'lead_agent')
  }
})

test('public reply and commands are projected independently, without tool output JSON', () => {
  const start = project([event('llm.ai.response', {
    id: 'ai-one', content: '老板，已核对代码。',
    tool_calls: [{ id: 'call-read', name: 'read_file', args: { path: 'src/index.html' } }],
  })])
  const end = project([event('llm.tool.result', {
    name: 'read_file', tool_call_id: 'call-read', status: 'success',
    content: '<html>SECRET_SOURCE\n'.repeat(10000),
  }, 2)])
  const visible = merge([...start, ...end])
  assert.equal(visible.length, 2)
  assert.equal(visible[0].kind, 'message')
  assert.equal(visible[0].messageId, 'ai-one')
  assert.equal(visible[1].label, 'read_file')
  assert.equal(visible[1].detail, 'src/index.html')
  assert.equal(visible[1].status, 'done')
  assert.ok(!JSON.stringify(visible).includes('SECRET_SOURCE'))
})

test('library and web searches keep their actual tool names and query after completion', () => {
  for (const name of ['search_office_library', 'web_search']) {
    const visible = merge(project([
      event('llm.ai.response', { tool_calls: [{ id: 'search', name, args: { query: '前端开发规范' } }] }),
      event('llm.tool.result', { name, tool_call_id: 'search', status: 'success', content: 'PRIVATE_DOCUMENT_BODY' }, 2),
    ]))
    assert.equal(visible.length, 1)
    assert.equal(visible[0].label, name)
    assert.equal(visible[0].detail, '前端开发规范')
    assert.equal(visible[0].status, 'done')
    assert.ok(!JSON.stringify(visible).includes('PRIVATE_DOCUMENT_BODY'))
  }
})

test('patch activity shows actual target paths without leaking patch body', () => {
  const patch = '*** Begin Patch\n*** Update File: /mnt/user-data/workspace/old.md\n*** Move to: /mnt/user-data/workspace/new.md\n@@\n-PRIVATE_OLD\n+PRIVATE_NEW\n*** End Patch'
  const visible = merge(project([
    event('llm.ai.response', { tool_calls: [{ id: 'patch', name: 'apply_patch', args: { patch } }] }),
    event('llm.tool.result', { name: 'apply_patch', tool_call_id: 'patch', status: 'error', content: 'PRIVATE_DISK_ERROR' }, 2),
  ]))
  assert.equal(visible[0].label, 'apply_patch')
  assert.equal(visible[0].detail, '/mnt/user-data/workspace/old.md · /mnt/user-data/workspace/new.md')
  assert.equal(visible[0].status, 'error')
  assert.ok(!JSON.stringify(visible).includes('PRIVATE_'))
})

test('hidden reasoning, middleware replies and PM protocol stay out of public chat', () => {
  const visible = project([
    event('llm.ai.response', { content: 'hidden' }, 1, { hidden: true }),
    event('llm.ai.response', { content: 'hidden', additional_kwargs: { hide_from_ui: true } }, 2),
    event('llm.ai.response', { content: 'middleware' }, 3, { caller: 'middleware:summarization' }),
    event('llm.ai.response', { content: [{ type: 'reasoning', text: 'secret' }, { type: 'text', text: '公开正文' }] }, 4),
    event('llm.tool.result', { name: 'PMResult', content: 'internal' }, 5),
    event('run.start', 'lifecycle', 6),
  ])
  assert.equal(visible.length, 1)
  assert.equal(visible[0].detail, '公开正文')
})

test('same tool IDs across Runs never merge and duplicate event reads are idempotent', () => {
  const one = project([event('llm.tool.result', { name: 'bash', tool_call_id: 'call', status: 'error' })])
  const two = project([{ ...event('llm.tool.result', { name: 'bash', tool_call_id: 'call' }), run_id: 'run-b' }])
  assert.equal(merge([...one, ...one, ...two]).length, 2)
  assert.equal(merge([...one, ...two])[0].status, 'error')
})

test('large arguments and subagent results cannot flood the activity list', () => {
  const visible = project([
    event('llm.ai.response', { tool_calls: [{ name: 'bash', id: 'shell', args: { command: 'x'.repeat(5000) } }] }),
    event('subagent.end', { result: 'huge'.repeat(5000), status: 'completed' }, 2),
  ])
  assert.equal(visible[0].detail.length, 240)
  assert.equal(visible[1].detail, 'completed')
})

test('parallel assignments from one model response retain both employees', () => {
  const visible = merge(project([event('llm.ai.response', { tool_calls: [
    { name: 'task', id: 'front', args: { subagent_type: 'frontend', description: '开发页面' } },
    { name: 'task', id: 'back', args: { subagent_type: 'backend', description: '开发接口' } },
  ] })]))
  assert.equal(visible.length, 2)
  assert.equal(visible[0].employeeId, 'frontend')
  assert.equal(visible[1].employeeId, 'backend')
})

test('delegated public prose has verified task identity and never uses raw step or tool text', () => {
  const step = (employee, task, content) => event('subagent.step', {
    employee_id: employee, task_id: task, kind: 'ai', message_id: 'shared-id',
    public_text: '开始搭建页面。', ...content,
  }, 2, { employee_id: employee, task_id: task })
  const visible = merge(project([
    step('front-a', 'task-a', { text: 'PRIVATE_REASONING' }),
    step('front-b', 'task-b', { text: 'PRIVATE_REASONING' }),
    step('front-a', 'task-a', { text: 'PRIVATE_REASONING' }),
    step('front-a', 'task-a', { kind: 'tool', public_text: 'PRIVATE_TOOL_OUTPUT' }),
    step('front-a', 'task-a', { employee_id: 'forged', public_text: 'PRIVATE_FORGED' }),
    step('front-a', 'task-a', { public_text: undefined, text: 'PRIVATE_LEGACY' }),
    event('llm.ai.response', { content: 'PRIVATE_SUBAGENT_CALLBACK' }, 4, { caller: 'subagent:front-a' }),
  ]))
  const prose = visible.filter(item => item.kind === 'message')
  assert.equal(prose.length, 2)
  assert.equal(prose[0].employeeId, 'front-a')
  assert.equal(prose[1].employeeId, 'front-b')
  assert.equal(prose[0].taskId, 'task-a')
  assert.ok(!JSON.stringify(visible).includes('PRIVATE_'))
})

test('one response keeps prose before its tool and hidden blocks never become prose', () => {
  const visible = project([event('llm.ai.response', { id: 'a', content: [
    { type: 'thinking', text: 'PRIVATE_THOUGHT' }, { type: 'text', text: 'PRIVATE_BLOCK', hidden: true },
    { type: 'text', text: '我先核对' }, { type: 'text', text: '项目文件。' },
  ], tool_calls: [{ name: 'read_file', id: 'read', args: { path: 'README.md' } }] })])
  assert.equal(visible[0].detail, '我先核对项目文件。')
  assert.equal(visible[0].hasToolCalls, true)
  assert.equal(visible[1].kind, 'tool')
  assert.equal(project([event('llm.ai.response', { content: { type: 'reasoning', text: 'PRIVATE' } })]).length, 0)
})

const historyFile = path.join(__dirname, '../client/src/components/office-chat/chatHistory.ts')
const historyExports = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(historyFile, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: historyExports, require: () => require('./i18n-test-helper.cjs') }, { filename: historyFile })

test('work duration spans queue and ask resumes but isolates employees and later work', () => {
  const first = { runId: 'first', employeeId: 'pm', workId: 'message:one', createdAt: '2026-10-28T00:01:00Z',
    workStartedAt: '2026-10-28T00:00:00Z', runtimeFinishedAt: '2026-10-28T00:02:00Z', status: 'interrupted', finalizing: false }
  let group = historyExports.workProgressGroups([first], new Set(['first']))[0]
  assert.equal(group.active, true)
  assert.equal(group.waiting, true)
  const resumed = { ...first, runId: 'resume', resumeSourceRunId: 'first', createdAt: '2026-10-28T00:05:00Z',
    runtimeFinishedAt: '2026-10-28T00:08:00Z', updatedAt: '2026-10-29T00:00:00Z', status: 'success' }
  const groups = historyExports.workProgressGroups([first, resumed, { ...resumed, employeeId: 'front', runId: 'parallel', resumeSourceRunId: null },
    { ...resumed, workId: 'message:two', runId: 'later', resumeSourceRunId: null }], new Set())
  assert.equal(groups.length, 3)
  group = groups.find(item => item.latest.runId === 'resume')
  assert.equal(group.active, false)
  assert.equal(historyExports.formatWorkDuration(group.finishedAt - group.startedAt), '8分钟 0秒')
  assert.equal(historyExports.workProgressGroups([{ ...resumed, finalizing: true }], new Set())[0].active, true)
  const recovered = historyExports.workProgressGroups([{ ...resumed, runtimeFinishedAt: null }], new Set())[0]
  assert.equal(recovered.active, false)
  assert.equal(recovered.finishedAt, undefined)
})

test('delegate preview scope is accepted only from matching server metadata', () => {
  const content = { task_id: 'task', employee_id: 'front', workspace_id: 'space', kind: 'ai', public_text: '[a](a.vue)' }
  const metadata = { task_id: 'task', employee_id: 'front', workspace_id: 'space' }
  assert.equal(project([event('subagent.step', content, 1, metadata)])[0].workspaceId, 'space')
  assert.equal(project([event('subagent.step', content, 1, { ...metadata, workspace_id: 'other' })])[0].workspaceId, undefined)
})

test('delegate generic and relative file links retain their task workspace', () => {
  const file = path.join(__dirname, '../client/src/components/office-chat/fileReferencePath.ts')
  const scope = {}
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: scope })
  assert.equal(scope.resolveFileReferencePath('/mnt/user-data/workspace/src/a.vue', undefined, 'space'), '/mnt/office-workspaces/space/workspace/src/a.vue')
  assert.equal(scope.resolveFileReferencePath('src/a.vue', undefined, 'space'), '/mnt/office-workspaces/space/workspace/src/a.vue')
  assert.equal(scope.resolveFileReferencePath('/mnt/office-workspaces/other/workspace/a.vue', undefined, 'space'), '/mnt/office-workspaces/other/workspace/a.vue')
  assert.equal(scope.resolveFileReferencePath('/mnt/user-data/outputs/a.md', undefined, 'space'), '/mnt/office-workspaces/space/outputs/a.md')
})

test('history dedup is identity scoped and only reconciles the final parent reply by text', () => {
  const progress = { runId: 'run-a', kind: 'message', seq: 1, messageId: 'progress', detail: '好了', hasToolCalls: true }
  const final = { ...progress, seq: 4, messageId: 'final', hasToolCalls: false }
  const history = [{ id: 'group-final', role: 'assistant', text: '好了', runId: 'run-a', employeeId: 'pm' }]
  assert.equal(historyExports.isSavedRunMessage(progress, history, [progress, final]), false)
  assert.equal(historyExports.isSavedRunMessage(final, history, [progress, final]), true)
  assert.equal(historyExports.isSavedRunMessage({ ...final, employeeId: 'front', taskId: 'task' }, history, [final]), false)
  assert.equal(historyExports.isSavedRunMessage({ ...final, runId: 'run-b' }, history, [final]), false)
  const privateHistory = [{ ...history[0], id: 'progress' }]
  assert.equal(historyExports.isSavedRunMessage(progress, privateHistory, [progress, final]), true)
  assert.equal(historyExports.isSavedRunMessage({ ...progress, messageId: 'repeat' }, privateHistory, [progress]), false)
})

test('checkpoint prose stays before its same-response tools, including while the Run is active', () => {
  const messages = [{ id: 'user', role: 'user', time: 100 },
    { id: 'progress', role: 'assistant', runId: 'run-a', time: 0 }]
  const prose = { runId: 'run-a', seq: 2, kind: 'message', messageId: 'progress' }
  const tool = { runId: 'run-a', seq: 2, kind: 'tool', createdAt: new Date(200).toISOString() }
  assert.equal(historyExports.runEventAnchor(tool, messages, [prose]), 2)
  messages.push({ id: 'second-user', role: 'user', time: 300 })
  messages.push({ id: 'final', role: 'assistant', runId: 'run-a', time: 400 })
  const final = { ...prose, seq: 5, messageId: 'final' }
  assert.equal(historyExports.runEventAnchor(tool, messages, [prose, final]), 2)
  assert.equal(historyExports.runEventAnchor({ ...tool, createdAt: new Date(350).toISOString() }, messages, [prose, final]), 3)
  assert.equal(historyExports.runEventAnchor({ ...tool, taskId: 'child' }, messages, [prose, final]), 2)
})

test('native targets discover resumed Runs, but do not repoll a drained ask or final answer', () => {
  const targets = [{ employeeId: 'pm', runId: 'ask' }, { employeeId: 'pm', runId: 'resume' }]
  const runs = [{ runId: 'ask', status: 'interrupted', eventsFinalized: true, lastEventSeq: 8 }]
  const cursors = new Map([['ask', 8]])
  assert.deepEqual(Array.from(historyExports.unobservedRunTargets(targets, runs, cursors, []), t => t.runId), ['resume'])
  assert.equal(historyExports.unobservedRunTargets(targets, runs, cursors, [targets[1]]).length, 0)
  runs.push({ runId: 'resume', status: 'success', eventsFinalized: true, lastEventSeq: 9 })
  cursors.set('resume', 9)
  assert.equal(historyExports.unobservedRunTargets(targets, runs, cursors, []).length, 0)
  runs[1].finalizing = true
  assert.equal(historyExports.unobservedRunTargets(targets, runs, cursors, []).length, 1)
})

test('group questions follow authoritative native targets after cancellation or failed resume', () => {
  const ask = { runId: 'ask', requestId: 'q', type: 'requested' }
  const runs = [{ runId: 'ask', threadId: 'pm-group', status: 'interrupted', createdAt: '2026-10-12T00:00:00Z' },
    { runId: 'failed-resume', threadId: 'pm-group', status: 'error', createdAt: '2026-10-12T00:01:00Z' }]
  assert.equal(historyExports.pendingQuestions([ask], runs, ['ask']).length, 1)
  assert.equal(historyExports.pendingQuestions([ask, { ...ask, type: 'responded', seq: Number.MAX_SAFE_INTEGER }], runs, ['ask']).length, 1)
  assert.equal(historyExports.pendingQuestions([ask], runs, []).length, 0)
})
test('a smaller or temporarily empty checkpoint never swallows displayed replies', () => {
  const old = [{ messageId: 'a', role: 'assistant', content: '此前的完整回复' }]
  assert.equal(historyExports.mergeConversationMessages(old, []).length, 1)
  const next = historyExports.mergeConversationMessages(old, [{ messageId: 'u', role: 'user', content: '继续' }])
  assert.equal(next[0].content, '此前的完整回复')
  assert.equal(next.length, 2)
  assert.equal(historyExports.mergeConversationMessages(next, old).length, 2)
})
test('activity folding never absorbs a reply or crosses a Run boundary', () => {
  const groups = historyExports.groupActivities([
    { key: '1', activity: { runId: 'a' } }, { key: '2', activity: { runId: 'a' } },
    { key: 'reply' }, { key: '3', activity: { runId: 'a' } }, { key: '4', activity: { runId: 'b' } },
  ])
  assert.equal(groups.length, 4)
  assert.equal(groups[0].length, 2)
  assert.equal(groups[1][0].key, 'reply')
})


test('only an unresolved question on the latest interrupted Thread Run remains answerable', () => {
  const request = {runId: 'old', requestId: 'q', source: 'clarification', type: 'requested'}
  const old = {runId: 'old', threadId: 'front-group', status: 'interrupted', createdAt: '2026-09-11T01:00:00Z'}
  const newer = {...old, runId: 'new', status: 'success', createdAt: '2026-09-11T02:00:00Z'}
  assert.equal(historyExports.pendingQuestions([request], [old]).length, 1)
  assert.equal(historyExports.pendingQuestions([request], [newer, old]).length, 0)
  assert.equal(historyExports.pendingQuestions([request], [{...old, status: 'running'}]).length, 0)
  assert.equal(historyExports.pendingQuestions([request, {...request, type: 'responded'}], [old]).length, 0)
  assert.equal(historyExports.pendingQuestions([request], [old, {...newer, threadId: 'other'}]).length, 1)
})

test('explicit employee mentions never get hijacked by an unrelated clarification', () => {
  const question = {runId: 'pm-old', requestId: 'q'}
  assert.equal(historyExports.selectReplyQuestion([question], '', true), undefined)
  assert.equal(historyExports.selectReplyQuestion([question], '', false), question)
  assert.equal(historyExports.selectReplyQuestion([question, {...question, requestId: 'q2'}], '', false), undefined)
  assert.equal(historyExports.selectReplyQuestion([question], 'pm-old:q', false), question)
})


test('terminal tool groups do not keep advertising execution forever', () => {
  assert.equal(historyExports.activityStatus('running', 'interrupted', false), 'stopped')
  assert.equal(historyExports.activityStatus('running', 'interrupted', true), 'waiting')
  assert.equal(historyExports.activityStatus('running', 'success', false), 'done')
  assert.equal(historyExports.activityStatus('done', 'interrupted', false), 'done')
  assert.equal(historyExports.activityStatus('running', 'running', false), 'running')
})

test('ask history distinguishes waiting, a real resume, unrelated work and cancellation', () => {
  const old = { runId: 'ask', employeeId: 'front', threadId: 'private', status: 'interrupted' }
  const resumed = { ...old, runId: 'resume', resumeSourceRunId: 'ask', status: 'running' }
  assert.equal(historyExports.runStatusLabel(old, false, true), '等待你的回复')
  assert.equal(historyExports.activityStatus('running', old.status, true), 'waiting')
  for (const status of ['running', 'success', 'error', 'interrupted']) {
    const linked = historyExports.hasResumedRun(old, [old, { ...resumed, status }])
    assert.equal(linked, true)
    assert.equal(historyExports.runStatusLabel(old, linked, false), '已继续')
    assert.equal(historyExports.activityStatus('running', old.status, false, linked), 'answered')
  }
  for (const unrelated of [
    { ...resumed, resumeSourceRunId: null },
    { ...resumed, employeeId: 'back' },
    { ...resumed, threadId: 'group' },
  ]) assert.equal(historyExports.hasResumedRun(old, [old, unrelated]), false)
  // A removed/locally answered question alone must not imply a successful Resume.
  assert.equal(historyExports.hasResumedRun(old, [old]), false)
  assert.equal(historyExports.runStatusLabel(old, false, false), '已中断')
  assert.equal(historyExports.activityStatus('running', old.status, false, false), 'stopped')
  assert.equal(historyExports.activityStatus('error', old.status, false, true), 'error')
  assert.equal(historyExports.activityStatus('done', old.status, false, true), 'done')
  assert.equal(historyExports.runStatusLabel(resumed), '工作中')
})


test('one streamed message identity updates in place instead of multiplying bubbles', () => {
  const first = project([event('llm.ai.response', {id:'same',content:'一部分'}, 1)])
  const next = project([event('llm.ai.response', {id:'same',content:'完整正文'}, 3)])
  const merged = merge([...first, ...next])
  assert.equal(merged.length,1)
  assert.equal(merged[0].detail,'完整正文')
})

test('history and event sender share one identity snapshot for the same employee', () => {
  const employee = {id:'pm',displayName:'新的姓名',avatar:'ash'}
  const history = {senderId:'pm',senderDisplayName:'项目经理',senderAvatar:'adam'}
  const sender = historyExports.chatSender('pm',[employee],history)
  assert.equal(sender.name,'项目经理')
  assert.equal(sender.avatar,'adam')
  assert.equal(historyExports.chatSender('pm',[employee]).name,'新的姓名')
})
