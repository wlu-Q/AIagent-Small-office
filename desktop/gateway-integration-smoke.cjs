// Real renderer + production main/preload + HTTP Gateway. No IPC mocks.
// Run against backend/tests/office_gateway_fixture.py only.
const assert = require('node:assert/strict')
const { once } = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { app, BrowserWindow } = require('electron')
const endpoint = new URL(process.env.DEER_FLOW_GATEWAY_URL || '')
assert.equal(endpoint.hostname, '127.0.0.1', 'integration fixture must be local')
const profile = fs.mkdtempSync(path.join(fs.realpathSync.native(os.tmpdir()), 'office-stage4-desktop-'))
const scenario = process.env.OFFICE_TEST_SCENARIO || 'standard'
app.setPath('userData', profile)
fs.writeFileSync(path.join(profile, 'language.json'), JSON.stringify({ language: 'zh-CN' }))
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
let window
async function api(route, options) {
  const response = await fetch(new URL(route, endpoint), options)
  assert.ok(response.ok, `${route}: ${response.status} ${response.ok ? '' : await response.text()}`)
  return response.json()
}
async function waitFor(expression, label, timeout = 25000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await window.webContents.executeJavaScript(`Boolean(${expression})`)) return
    await sleep(150)
  }
  throw new Error(`Timeout: ${label}`)
}
async function waitForGroupCompletion() {
  const deadline = Date.now() + 30000
  do {
    const execution = await api('/api/office/group/execution')
    if (execution.runTargets.length === 0) return
    await sleep(150)
  } while (Date.now() < deadline)
  assert.equal((await api('/api/office/group/execution')).runTargets.length, 0, 'group Run cleanup must complete')
}
async function setEditor(text) {
  await window.webContents.executeJavaScript(`(() => {
    const editor = document.querySelector('[data-office-message-input]');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(editor, ${JSON.stringify(text)});
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  })()`)
  // Let the controlled input, mention selection and reply mode commit before clicking.
  await window.webContents.executeJavaScript('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
}
async function send(text) {
  if (text.startsWith('E2E_') && !text.startsWith('E2E_EMPLOYEE')) {
    await setEditor('@')
    await waitFor("document.querySelector('[data-office-mention=\"pm\"]')", 'PM mention')
    await window.webContents.executeJavaScript("document.querySelector('[data-office-mention=\"pm\"]').click()")
    text = `@项目经理 ${text}`
  }
  await setEditor(text)
  await waitFor("document.querySelector('[data-office-send]')?.disabled === false", 'send enabled')
  await window.webContents.executeJavaScript("document.querySelector('[data-office-send]').click()")
  await waitFor("document.querySelector('[data-office-message-input]')?.value === '' && !document.querySelector('[data-office-send-status=\"sending\"]')", 'message accepted')
}
async function openChat() {
  await waitFor("document.querySelector('[data-office-chat-launcher]')", 'office ready')
  await window.webContents.executeJavaScript("document.querySelector('[data-office-chat-launcher][aria-expanded=false]')?.click()")
  await waitFor("document.querySelector('[data-office-chat-launcher][aria-expanded=true]') && document.querySelector('[data-office-message-input]')", 'chat ready')
}
async function reloadRenderer() {
  const loaded = once(window.webContents, 'did-finish-load', { signal: AbortSignal.timeout(25000) })
  window.webContents.reload()
  await loaded
}
async function reloadChat() {
  await reloadRenderer()
  await openChat()
}
async function run() {
  const setup = await api('/api/office/bootstrap')
  if (scenario === 'bootstrap') assert.equal(setup.initialized, false)
  else assert.equal(setup.companyName, '第四阶段联调', 'never run these scenarios on a user office')
  if (scenario !== 'restart-verify') await api('/api/office/group/reset', { method: 'POST' })
  require('./main.cjs')
  await app.whenReady()
  while (!(window = BrowserWindow.getAllWindows()[0])) await sleep(50)
  window.webContents.setBackgroundThrottling(false)
  window.webContents.on('console-message', ({ level, message }) => { if (level === 'warning' || level === 'error') console.error(`RENDERER: ${message}`) })
  window.webContents.on('render-process-gone', (_event, details) => console.error('RENDERER_GONE', details))
  if (scenario === 'bootstrap') {
    await require('./bootstrap-smoke.cjs').run({ window, api, waitFor, endpoint, profile, reloadRenderer })
    return
  }
  await openChat()
  const service = await window.webContents.executeJavaScript('window.officeDesktopBridge.getServiceStatus()')
  assert.equal(service.lifecycle, 'connected')
  assert.equal(service.detail, require('../contracts/locales/zh-CN.json')[require('../contracts/desktop-gateway-status.json').connected])
  assert.equal(await window.webContents.executeJavaScript("!!document.querySelector('[data-office-workflow]')"), false)

  if (scenario === 'visualization') {
    await require('./visualization-smoke.cjs').run({ window, api, send, waitFor, endpoint })
    return
  }
  if (scenario === 'delegation-limits') {
    await require('./delegation-limits-smoke.cjs').run({ window, api, send, waitFor })
    return
  }
  if (scenario === 'workspaces') {
    await require('./task-workspaces-smoke.cjs').run({ window, api, send, waitFor, reloadChat })
    return
  }
  if (scenario === 'terminal') {
    await require('./terminal-smoke.cjs').run({ window, api, send, waitFor })
    return
  }
  if (scenario === 'delivery') {
    await require('./delivery-smoke.cjs').run({ window, api, send, waitFor })
    return
  }
  if (scenario === 'private-message-identity') {
    const evaluate = expression => window.webContents.executeJavaScript(expression)
    const slow = '请稍等再回复 E2E_SLOW'
    const repeated = '请回复收到 E2E_EMPLOYEE'
    const count = text => `[...document.querySelectorAll('[data-message-role="user"]')].filter(node => node.textContent.includes(${JSON.stringify(text)})).length`
    await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
    await send(slow)
    await waitFor("document.querySelector('[data-office-ai-loading]')", 'private employee is working')
    await send(repeated)
    await send(repeated)
    const queued = (await api('/api/office/queue')).items.filter(item => item.targetEmployeeId === 'writer')
    assert.equal(queued.length, 2, 'both identical messages queue separately')
    assert.equal(await evaluate(count(repeated)), 2)
    await evaluate("document.querySelector('[data-office-group]').click()")
    assert.equal(await evaluate(count(repeated)), 0, 'private messages stay out of group')
    await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
    await waitFor(`${count(repeated)} === 2`, 'cached pending messages return with private conversation')
    const deadline = Date.now() + 65000
    let runs = []
    while (Date.now() < deadline) {
      runs = (await api('/api/office/employees/writer/runs?conversationKind=private')).runs
      if (runs.length === 3 && runs.every(run => run.status === 'success')) break
      await sleep(200)
    }
    assert.equal(runs.length, 3)
    assert.ok(runs.every(run => run.status === 'success'), 'each send executes once and FIFO drains')
    const history = (await api('/api/office/employees/writer/conversations/private/messages')).messages
    const users = history.filter(message => message.role === 'user')
    assert.equal(users.length, 3)
    assert.equal(new Set(users.map(message => message.sourceMessageId)).size, 3)
    assert.ok(users.every(message => message.sourceMessageId && message.sourceMessageId !== message.messageId))
    await waitFor(`!document.querySelector('[data-office-ai-loading]') && ${count(slow)} === 1 && ${count(repeated)} === 2`, 'history replaces temporary messages without duplicates')
    await evaluate("document.querySelector('[data-office-group]').click()")
    await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
    await waitFor(`${count(slow)} === 1 && ${count(repeated)} === 2`, 'switch back preserves exact counts')
    await reloadChat()
    await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
    await waitFor(`${count(slow)} === 1 && ${count(repeated)} === 2`, 'history reload preserves distinct identical sends')
    console.log('PRIVATE_MESSAGE_IDENTITY_OK: native/source IDs, FIFO, identical sends, conversation switch, reload')
    return
  }
  if (scenario === 'file-preview') {
    await require('./file-preview-smoke.cjs').run({ window, api, send, waitFor, endpoint })
    return
  }

  if (scenario === 'model-settings' || scenario === 'model-edit') {
    const click = selector => window.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(selector)}).click()`)
    const chooseThinking = async value => {
      await window.webContents.executeJavaScript(`document.querySelector('[data-office-thinking-select] [role="button"]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}))`)
      await waitFor(`document.querySelector('[data-office-thinking-option="${value}"]')`, 'thinking options loaded')
      await click(`[data-office-thinking-option="${value}"]`)
      await waitFor(`!document.querySelector('[role="listbox"]')`, 'thinking menu closed')
    }
    const input = (name, value) => window.webContents.executeJavaScript(`(() => {
      const el=document.querySelector('input[name="${name}"]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(value)});
      el.dispatchEvent(new Event('input',{bubbles:true}));
    })()`)
    await click('[aria-label="打开办公室设置"]')
    await waitFor(`document.querySelector('[data-office-model-add]')?.disabled === false`, 'model list from Gateway')
    await click('[data-office-model-add]')
    await waitFor(`document.querySelector('[data-office-provider="openai"]')`, 'provider chooser')
    await click('[data-office-provider="openai"]')
    await input('modelName', 'protocol-controlled')
    await input('baseUrl', `${endpoint.origin}/test-model/v1`)
    await input('apiKey', 'fixture-key')
    await chooseThinking('max')
    await click('[data-office-model-submit]')
    await waitFor(`document.querySelectorAll('[data-office-model-list] tbody tr').length === 2 && !document.querySelector('[data-office-add-model-dialog]')`, 'real probe then persisted model', 95000)
    const catalogue = await api('/api/office/models')
    const newId = catalogue.models.find(m => m.modelName === 'protocol-controlled').id
    assert.equal(catalogue.models.find(m => m.id === newId).thinkingLevel, 'max')
    assert.equal(await window.webContents.executeJavaScript(`document.querySelector('[data-office-model-thinking="${newId}"]').textContent`), '最高')
    assert.ok((await api('/test-model/requests')).every(r => r.reasoningEffort === 'max'), 'connection probe uses saved effort')
    assert.ok(catalogue.models.find(m => m.id === 'office-default').isDefault)
    await click(`[data-office-model-default="${newId}"]`)
    await waitFor(`document.querySelector('[data-office-model-default="${newId}"]').textContent === '当前默认'`, 'real default change')
    if (scenario === 'model-edit') {
      const before = (await api('/api/office/models')).models.find(m => m.id === newId)
      await click(`[data-office-model-edit="${newId}"]`)
      await waitFor(`document.querySelector('input[name="modelName"]')?.value === 'protocol-controlled' && !document.querySelector('input[name="modelName"]').disabled`, 'edit model prefilled')
      assert.equal(await window.webContents.executeJavaScript(`document.querySelector('input[name="apiKey"]').value`), '')
      await input('modelName', 'protocol-edited')
      await chooseThinking('xhigh')
      await click('[data-office-model-submit]')
      await waitFor(`!document.querySelector('[data-office-add-model-dialog]') && document.body.innerText.includes('protocol-edited')`, 'edit verifies and saves through real Gateway', 95000)
      const models = (await api('/api/office/models')).models
      assert.equal(models.length, 2)
      assert.deepEqual(models.find(m => m.id === newId), { ...before, modelName: 'protocol-edited', thinkingLevel: 'xhigh', thinkingLabel: '极高' })
      assert.ok((await api('/test-model/requests')).some(r => r.reasoningEffort === 'xhigh'), 'edit probe uses changed effort')
      const detail = await api(`/api/office/models/${newId}`)
      assert.equal(detail.apiKeyConfigured, true)
      assert.ok(!JSON.stringify(detail).includes('fixture-key'))
      await click('[aria-label="关闭办公室设置"]')
      assert.equal((await api('/api/office/employees/pm')).employee.modelProfileId, 'office-default')
      await send('E2E_ASK 确认一下页面颜色')
      await waitFor(`document.querySelector('[data-office-human-input]')`, 'PM waits for user')
      await click('[aria-label="打开办公室设置"]')
      await waitFor(`document.querySelector('[data-office-model-edit="office-default"]')`, 'model edit while employee busy')
      await click('[data-office-model-edit="office-default"]')
      await waitFor(`document.querySelector('[data-office-model-submit]')?.disabled === true && document.querySelector('input[name="modelName"]')?.disabled === true && document.querySelector('[data-office-add-model-dialog] form')?.getAttribute('aria-busy') === 'false'`, 'busy model read only')
      const blocked = await fetch(new URL('/api/office/models/office-default', endpoint), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modelName: 'cannot-change', baseUrl: `${endpoint.origin}/test-model/v1`, apiKey: '', supportsVision: false }),
      })
      assert.equal(blocked.status, 409)
      await click('[aria-label="关闭编辑模型"]')
      await waitFor(`!document.querySelector('[data-office-add-model-dialog]')`, 'busy dialog closed')
      await click('[aria-label="关闭办公室设置"]')
      await send('蓝色就行')
      await waitFor(`document.body.innerText.includes('E2E_ASK_DONE')`, 'original model resumes after rejected edit')
      await reloadChat()
      await click('[aria-label="打开办公室设置"]')
      await waitFor(`document.querySelector('[data-office-model-row="${newId}"]')?.textContent.includes('protocol-edited')`, 'saved edit survives reload')
      console.log('LIVE_MODEL_EDIT_OK: real page/IPC/Gateway/probe, retained secret/default/identity/date/binding, busy rejection, ask resume, reload')
      return
    }
    await click('[aria-label="关闭办公室设置"]')
    assert.equal((await api('/api/office/employees/pm')).employee.modelProfileId, 'office-default', 'global change leaves existing PM fixed')
    const hired = await api('/api/office/employees', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: 'new-model-worker', templateId: 'frontend-engineer', displayName: '新模型员工', avatar: 'adam' }) })
    assert.equal(hired.employee.modelProfileId, newId, 'new employee snapshots current default')
    await click('[data-office-employee-editor-launcher]')
    await waitFor(`document.querySelector('[data-edit-employee-item="pm"]')`, 'employee editor')
    await click('[data-edit-employee-item="pm"]')
    await waitFor(`document.querySelector('[data-edit-employee-model] [role="button"]')?.getAttribute('aria-disabled') !== 'true' && document.querySelector('[data-edit-employee-prompt]')`, 'idle PM editable')
    await window.webContents.executeJavaScript(`document.querySelector('[data-edit-employee-model] [role="button"]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}))`)
    await waitFor(`document.querySelector('[data-employee-model-option="${newId}"]')`, 'registered model in employee menu')
    assert.ok(await window.webContents.executeJavaScript(`document.querySelector('[data-employee-model-option="${newId}"]').textContent.includes('OpenAI - protocol-controlled - 最高')`))
    await click(`[data-employee-model-option="${newId}"]`)
    assert.equal((await api('/api/office/employees/pm')).employee.modelProfileId, 'office-default', 'draft is not a binding')
    await click('[data-save-employee]')
    await waitFor(`document.querySelector('[data-employee-edit-status]')?.textContent === '已保存。'`, 'employee model saved through real Gateway')
    assert.equal((await api('/api/office/employees/pm')).employee.modelProfileId, newId)
    await click('[data-close-employee-editor]')
    await send('E2E_ASK 请确认范围，接着我要切换模型')
    await waitFor(`document.querySelector('[data-office-human-input]')`, 'new model runs ask via HTTP streaming')
    const pending = (await api('/api/office/group/execution')).runTargets.find(t => t.employeeId === 'pm')
    const blocked = await fetch(new URL('/api/office/employees/pm/personal-info', endpoint), {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ modelProfileId: 'office-default' }),
    })
    assert.equal(blocked.status, 409, 'ask waiting cannot change employee model')
    await window.webContents.executeJavaScript(`window.officeDesktopBridge.setOfficeDefaultModel('office-default')`)
    await send('E2E_QUEUED 切换后排队的新工作')
    await waitFor(`document.body.innerText.includes('排队中')`, 'ask holds queue after default switch')
    await send('研发部门')
    await waitFor(`document.body.innerText.includes('E2E_ASK_DONE') && document.body.innerText.includes('E2E_DIRECT_DONE')`, 'resume old model and drain FIFO', 40000)
    const contracts = await api('/test-model/contracts')
    assert.equal(contracts.find(r => r.runId === pending.runId).modelId, newId)
    assert.equal(contracts.find(r => r.resumeSource === pending.runId).modelId, newId)
    assert.ok(contracts.length >= 3)
    assert.ok(contracts.every(r => r.modelId === newId), 'FIFO also uses the fixed employee model after global change')
    assert.equal((await api('/api/office/employees/new-model-worker')).employee.modelProfileId, newId)
    await window.webContents.executeJavaScript(`window.officeDesktopBridge.setOfficeDefaultModel(${JSON.stringify(newId)})`)
    await api('/api/office/employees/writer/personal-info', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modelProfileId: newId }) })
    await send('E2E_DELEGATE 给员工写一段产品介绍')
    await waitFor(`document.body.innerText.includes('E2E_DELEGATE_DONE')`, 'new model delegates', 40000)
    await waitForGroupCompletion()
    await click('[data-office-employee="writer"]')
    await send('E2E_EMPLOYEE 私聊介绍产品')
    await waitFor(`document.body.innerText.includes('员工产品介绍')`, 'direct employee uses selected model')
    const requests = await api('/test-model/requests')
    assert.ok(requests.some(r => r.tools.includes('task') && r.reasoningEffort === 'max'), `PM uses saved effort during execution: ${JSON.stringify(requests)}`)
    assert.ok(requests.filter(r => r.tools.includes('report_to_pm') && !r.tools.includes('task') && r.reasoningEffort === 'max').length >= 2, `delegated and direct employee use saved effort: ${JSON.stringify(requests)}`)
    await reloadChat()
    await click('[aria-label="打开办公室设置"]')
    await waitFor(`document.querySelector('[data-office-model-default="${newId}"]')?.textContent === '当前默认'`, 'model persistence after renderer reload')
    console.log('LIVE_MODEL_SETTINGS_OK: page model save; creation snapshots default; existing binding fixed; busy rejected; ask/FIFO/delegation/reload')
    return
  }

  if (scenario === 'restart-prepare') {
    await send('E2E_ASK 重启后回答部门范围')
    await waitFor("document.querySelector('[data-office-human-input]')", 'ask persisted before process restart')
    const source = (await api('/api/office/group/execution')).runTargets.find(t => t.employeeId === 'pm')
    assert.ok(source?.workId)
    fs.writeFileSync(process.env.OFFICE_TEST_RESTART_FILE, JSON.stringify(source))
    console.log('LIVE_GATEWAY_RESTART_PREPARED')
    return
  }
  if (scenario === 'restart-verify') {
    const source = JSON.parse(fs.readFileSync(process.env.OFFICE_TEST_RESTART_FILE, 'utf8'))
    await waitFor("document.querySelector('[data-office-human-input]')", 'ask restored from SQLite after process restart')
    const restored = (await api('/api/office/group/execution')).runTargets.find(t => t.employeeId === 'pm')
    assert.deepEqual(restored, source)
    assert.equal((await api('/api/office/employees')).employees.find(e => e.id === 'pm').state, 'working')
    const before = (await api('/api/office/employees/pm/runs?conversationKind=group')).runs
    await send('E2E_QUEUED 重启后排队的无关任务')
    await waitFor("document.body.innerText.includes('排队中')", 'restored ask blocks unrelated PM work')
    assert.equal((await api('/api/office/employees/pm/runs?conversationKind=group')).runs.length, before.length)
    await send('研发部门')
    await waitFor("document.body.innerText.includes('E2E_ASK_DONE') && document.body.innerText.includes('E2E_DIRECT_DONE')", 'resumed work completes and queued work drains')
    await waitForGroupCompletion()
    const runs = (await api('/api/office/employees/pm/runs?conversationKind=group')).runs
    const resumed = runs.filter(r => r.resumeSourceRunId === source.runId)
    assert.equal(resumed.length, 1)
    assert.equal(resumed[0].workId, source.workId)
    assert.equal(resumed[0].status, 'success')
    console.log('LIVE_GATEWAY_PROCESS_RESTART_RESUME_OK')
    return
  }

  await send('E2E_DIRECT 当前前端架构是什么？')
  await waitFor("document.body.innerText.includes('E2E_DIRECT_DONE')", 'direct native final answer')
  await waitForGroupCompletion()
  console.log('LIVE_GATEWAY_DIRECT_OK')

  await send('E2E_ASK 请先确认部门范围')
  await waitFor("document.querySelector('[data-office-human-input]')", 'native ask appears')
  const source = (await api('/api/office/group/execution')).runTargets.find(t => t.employeeId === 'pm')
  assert.ok(source)
  assert.equal((await api('/api/office/employees')).employees.find(e => e.id === 'pm').state, 'working')
  await send('E2E_QUEUED 等待当前问题结束后再处理')
  await waitFor("document.body.innerText.includes('排队中')", 'unrelated PM work queues during ask')
  assert.equal((await api('/api/office/group/execution')).runTargets.find(t => t.employeeId === 'pm').runId, source.runId)
  await reloadChat()
  await waitFor("document.querySelector('[data-office-human-input]')", 'ask restored after reload')
  await send('研发部门')
  await waitFor("!document.querySelector('[data-office-human-input]')", 'ask consumed')
  await waitFor("document.body.innerText.includes('E2E_ASK_DONE')", 'resumed final answer')
  const runs = (await api('/api/office/employees/pm/runs?conversationKind=group')).runs
  const resumed = runs.find(r => r.resumeSourceRunId === source.runId)
  assert.ok(resumed)
  assert.equal(resumed.workId, source.workId)
  console.log('LIVE_GATEWAY_ASK_RELOAD_RESUME_OK')

  await send('E2E_DELEGATE 请文档员工写产品介绍')
  await waitFor("document.body.innerText.includes('E2E_DELEGATE_DONE')", 'native employee delegation', 40000)
  await waitForGroupCompletion()
  const delegated = (await api('/api/office/employees/pm/runs?conversationKind=group')).runs.find(r => r.taskSummary.includes('E2E_DELEGATE'))
  const evidence = await api(`/api/office/employees/pm/runs/${delegated.runId}/events`)
  assert.ok(JSON.stringify(evidence.events).includes('员工产品介绍'), 'actual employee result must reach native events, not just PM final prose')
  console.log('LIVE_GATEWAY_DELEGATION_OK')

  await send('E2E_SLOW 请执行可暂停工作')
  await waitFor("document.querySelector('[data-office-pause]')?.disabled === false", 'pause available')
  await window.webContents.executeJavaScript("document.querySelector('[data-office-pause]').click()")
  await waitFor("document.body.innerText.includes('之前的群聊任务已暂停')", 'native pause settles')
  await send('继续执行')
  await waitFor("document.body.innerText.includes('E2E_SLOW_DONE')", 'native checkpoint continues', 30000)
  console.log('LIVE_GATEWAY_PAUSE_CONTINUE_OK')
  await waitForGroupCompletion()

  await send('E2E_SLOW 先处理这项旧工作')
  await waitFor("document.querySelector('[data-office-pause]')?.disabled === false", 'second pause available')
  const oldRun = (await api('/api/office/group/execution')).runTargets.find(target => target.employeeId === 'pm')
  assert.ok(oldRun)
  await send('E2E_QUEUED 这条旧消息先等着')
  const oldQueue = (await api('/api/office/queue')).items.find(item => item.text.includes('这条旧消息先等着'))
  assert.ok(oldQueue)
  await window.webContents.executeJavaScript("document.querySelector('[data-office-pause]').click()")
  await waitFor("document.body.innerText.includes('之前的群聊任务已暂停')", 'old work remains paused')
  await send('E2E_ASK_AFTER_PAUSE 新需求先确认一下部门')
  await waitFor("document.querySelector('[data-office-human-option]')", 'new ask after old pause')
  const newAsk = (await api('/api/office/group/execution')).runTargets.find(target => target.employeeId === 'pm')
  assert.ok(newAsk && newAsk.runId !== oldRun.runId)
  await window.webContents.executeJavaScript("document.querySelector('[data-office-human-option]').click()")
  await waitFor("document.querySelector('[data-office-message-input]')?.value === '产品部门'", 'option fills answer')
  await window.webContents.executeJavaScript("document.querySelector('[data-office-send]').click()")
  await waitFor("!document.querySelector('[data-office-human-input]')", 'new ask consumed despite old pause')
  await waitForGroupCompletion()
  const afterPauseRuns = (await api('/api/office/employees/pm/runs?conversationKind=group')).runs
  const newResumes = afterPauseRuns.filter(run => run.resumeSourceRunId === newAsk.runId)
  assert.equal(newResumes.length, 1)
  assert.equal(newResumes[0].status, 'success')
  assert.equal(newResumes[0].workId, newAsk.workId)
  assert.equal(afterPauseRuns.find(run => run.runId === oldRun.runId).status, 'interrupted')
  assert.ok(!afterPauseRuns.some(run => run.resumeSourceRunId === oldRun.runId))
  const heldState = await api('/api/office/group/execution')
  assert.equal(heldState.paused, true)
  assert.deepEqual(heldState.heldQueueIds, [oldQueue.queueId])
  assert.deepEqual((await api('/api/office/queue')).items.map(item => item.queueId), [oldQueue.queueId])
  await waitFor("Array.from(document.querySelectorAll('[data-message-role=user]')).filter(row => row.textContent.includes('产品部门')).length === 1", 'answer visible exactly once')
  console.log('LIVE_GATEWAY_NEW_ASK_AFTER_PAUSE_OK')
  await window.webContents.executeJavaScript(`document.querySelector('[data-office-queue-delete="${oldQueue.queueId}"]').click()`)
  await waitFor("document.querySelectorAll('[data-office-queue-item]').length === 0", 'remove only the test queued message')

  await send('E2E_ASK 请确认另一次范围')
  await waitFor("document.querySelector('[data-office-human-input]')", 'second ask appears')
  const pending = (await api('/api/office/group/execution')).runTargets.find(t => t.employeeId === 'pm')
  await window.webContents.executeJavaScript(`window.officeDesktopBridge.cancelOfficeRun('pm', ${JSON.stringify(pending.runId)})`)
  await waitFor("!document.querySelector('[data-office-human-input]')", 'cancelled ask disappears')
  await waitForGroupCompletion()
  console.log('LIVE_GATEWAY_CANCEL_ASK_OK')
  await window.webContents.executeJavaScript("document.querySelector('[data-office-employee=\"writer\"]').click()")
  await send('E2E_EMPLOYEE 私聊介绍产品')
  await waitFor("document.body.innerText.includes('员工产品介绍')", 'private employee conversation')
  console.log('LIVE_GATEWAY_PRIVATE_OK')
  const screenshot = path.join(profile, 'verified.png')
  fs.writeFileSync(screenshot, (await window.webContents.capturePage()).toPNG())
  console.log(`LIVE_GATEWAY_SCREENSHOT=${screenshot}`)
}
run().then(() => app.quit()).catch(async error => {
  console.error(error)
  if (scenario === 'model-settings') {
    console.error('MODEL_TEST_FINAL_QUEUE', JSON.stringify(await api('/api/office/queue')))
    console.error('MODEL_TEST_REQUESTS', JSON.stringify(await api('/test-model/requests')))
  }
  if (window && !window.isDestroyed()) {
    console.error(await window.webContents.executeJavaScript('document.body.innerText'))
    if (scenario === 'file-preview') {
      const output = path.resolve(__dirname, '../output/playwright')
      fs.mkdirSync(output, { recursive: true })
      fs.writeFileSync(path.join(output, 'file-preview-failure.png'), (await window.webContents.capturePage()).toPNG())
      console.error('PREVIEW_DIAGNOSTICS', await window.webContents.executeJavaScript(`(() => {
        const editor=document.querySelector('[data-office-code-preview]'); return {html:editor?.outerHTML.slice(0,4000), width:editor?.clientWidth,height:editor?.clientHeight}
      })()`))
    }
  }
  app.exit(1)
})
