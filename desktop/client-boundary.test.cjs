const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const root = path.resolve(__dirname, '..')
const clientSource = path.join(root, 'client', 'src')
const mainPath = path.join(__dirname, 'main.cjs')
const preloadPath = path.join(__dirname, 'preload.cjs')
const gatewayStatusContractPath = path.join(root, 'contracts', 'desktop-gateway-status.json')

const expectedInvokeChannels = [
  'office-desktop:listExecutionApprovals',
  'office-desktop:decideExecutionApproval',
  'office-desktop:listMcpServices', 'office-desktop:changeMcpService', 'office-desktop:getOfficeEmployeeTools',
  'office-desktop:saveOfficeEmployeeTools',
  'office-desktop:library',
  'office-desktop:openExternalUrl',
  'office-desktop:previewOfficeFile',
  'office-desktop:revealOfficeFile',
  'office-desktop:getServiceStatus',
  'office-desktop:pingService',
  'office-desktop:getWorkspaceSummary',
  'office-desktop:selectAndBindWorkspace',
  'office-desktop:listOfficeEmployees',
  'office-desktop:getOfficeEmployee',
  'office-desktop:listOfficeEmployeeTemplates',
  'office-desktop:listModels',
  'office-desktop:listOfficeModels',
  'office-desktop:getOfficeModel',
  'office-desktop:updateOfficeModel',
  'office-desktop:addOfficeModel',
  'office-desktop:setOfficeDefaultModel',
  'office-desktop:listSkills',
  'office-desktop:getOfficeBootstrap',
  'office-desktop:getOfficeSettings',
  'office-desktop:getLanguage',
  'office-desktop:setLanguage',
  'office-desktop:saveOfficeSettings',
  'office-desktop:getUserMemory',
  'office-desktop:getEmployeeMemory',
  'office-desktop:listWorkLogDays',
  'office-desktop:getWorkLogDay',
  'office-desktop:createMemoryFact',
  'office-desktop:updateMemoryFact',
  'office-desktop:deleteMemoryFact',
  'office-desktop:testOfficeBootstrapModel',
  'office-desktop:initializeOfficeBootstrap',
  'office-desktop:createOfficeEmployee',
  'office-desktop:saveOfficeEmployeePersonalInfo',
  'office-desktop:deleteOfficeEmployee',
  'office-desktop:sendOfficeMessage',
  'office-desktop:listOfficeGroupMessages',
  'office-desktop:listOfficeConversationMessages',
  'office-desktop:listOfficeQueue',
  'office-desktop:editOfficeQueue',
  'office-desktop:deleteOfficeQueue',
  'office-desktop:getCurrentOfficeWorkflow',
  'office-desktop:getOfficeWorkflow',
  'office-desktop:getOfficeGroupExecution',
  'office-desktop:pauseOfficeGroup',
  'office-desktop:resetOfficeGroup',
  'office-desktop:cancelOfficeRun',
  'office-desktop:resumeOfficeRun',
  'office-desktop:getOfficeRun',
  'office-desktop:listOfficeRuns',
  'office-desktop:listOfficeRunEvents',
  'office-desktop:getZoomFactor',
  'office-desktop:setZoomFactor',
  'office-desktop:zoomBy',
  'office-desktop:resetZoom',
]
const expectedBridgeMethods = [
  'listExecutionApprovals',
  'decideExecutionApproval',
  'listMcpServices', 'changeMcpService', 'getOfficeEmployeeTools',
  'saveOfficeEmployeeTools',
  'officeLibrary',
  'openExternalUrl',
  'previewOfficeFile',
  'revealOfficeFile',
  'getServiceStatus',
  'pingService',
  'getWorkspaceSummary',
  'selectAndBindWorkspace',
  'listOfficeEmployees',
  'getOfficeEmployee',
  'listOfficeEmployeeTemplates',
  'listModels',
  'listOfficeModels',
  'getOfficeModel',
  'updateOfficeModel',
  'addOfficeModel',
  'setOfficeDefaultModel',
  'listSkills',
  'getOfficeBootstrap',
  'getOfficeSettings',
  'getLanguage',
  'setLanguage',
  'saveOfficeSettings',
  'getUserMemory',
  'getEmployeeMemory',
  'listWorkLogDays',
  'getWorkLogDay',
  'createMemoryFact',
  'updateMemoryFact',
  'deleteMemoryFact',
  'testOfficeBootstrapModel',
  'initializeOfficeBootstrap',
  'createOfficeEmployee',
  'saveOfficeEmployeePersonalInfo',
  'deleteOfficeEmployee',
  'sendOfficeMessage',
  'listOfficeGroupMessages',
  'listOfficeConversationMessages',
  'listOfficeQueue',
  'editOfficeQueue',
  'deleteOfficeQueue',
  'getCurrentOfficeWorkflow',
  'getOfficeWorkflow',
  'getOfficeGroupExecution',
  'pauseOfficeGroup',
  'resetOfficeGroup',
  'cancelOfficeRun',
  'resumeOfficeRun',
  'getOfficeRun',
  'listOfficeRuns',
  'listOfficeRunEvents',
  'getZoomFactor',
  'setZoomFactor',
  'zoomBy',
  'resetZoom',
  'onServiceEvent',
]
const expectedRendererEventChannel = 'office-desktop:event'
const expectedBridgeEventTypes = ['service.status', 'workspace.updated']

function read(file) {
  return fs.readFileSync(file, 'utf8')
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function countCalls(source, callee) {
  return [...source.matchAll(new RegExp(`${escapeRegExp(callee)}\\s*\\(`, 'g'))].length
}

function literalFirstArguments(source, callee) {
  return [
    ...source.matchAll(
      new RegExp(`${escapeRegExp(callee)}\\s*\\(\\s*(['"])([^'"]+)\\1`, 'g'),
    ),
  ].map((match) => match[2])
}

function assertStaticLiteralCalls(source, callee) {
  assert.equal(
    literalFirstArguments(source, callee).length,
    countCalls(source, callee),
    `${callee} must always receive a static string literal as its first argument`,
  )
}

function memberAccesses(source, objectName) {
  return [
    ...source.matchAll(new RegExp(`${escapeRegExp(objectName)}\\.([A-Za-z_$][\\w$]*)\\b`, 'g')),
  ].map((match) => match[1])
}

function assertDirectCallsOnly(source, callee) {
  const memberReferences = [
    ...source.matchAll(new RegExp(`${escapeRegExp(callee)}\\b`, 'g')),
  ].length
  assert.equal(memberReferences, countCalls(source, callee), `${callee} cannot be aliased or passed around`)
}

function assertExactMembers(actual, expected, label) {
  assert.equal(actual.length, new Set(actual).size, `${label} contains duplicates`)
  assert.deepEqual([...actual].sort(), [...expected].sort(), `${label} changed outside the approved whitelist`)
}

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name)
    return entry.isDirectory() ? walk(target) : [target]
  })
}

function unresolvedRelativeImports() {
  const sourceExtensions = new Set(['.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs'])
  const importPattern = /(?:import\s+(?:[^'\"]+?\s+from\s+)?|export\s+[^'\"]*?from\s+|require\()\s*['\"](\.[^'\"]+)['\"]/g
  const candidatesFor = (fromFile, specifier) => {
    const base = path.resolve(path.dirname(fromFile), specifier)
    if (path.extname(base)) return [base]
    return [
      ...['.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs', '.json'].map((extension) => base + extension),
      ...['index.ts', 'index.tsx', 'index.js', 'index.jsx', 'index.cjs', 'index.mjs'].map((name) => path.join(base, name)),
    ]
  }

  const files = [
    ...walk(clientSource),
    ...walk(__dirname).filter((file) => !file.endsWith('.test.cjs')),
  ].filter((file) => sourceExtensions.has(path.extname(file)))

  const missing = []
  for (const file of files) {
    const source = read(file)
    for (const match of source.matchAll(importPattern)) {
      if (!candidatesFor(file, match[1]).some((candidate) => fs.existsSync(candidate))) {
        missing.push(`${path.relative(root, file)} -> ${match[1]}`)
      }
    }
  }
  return missing
}

function loadDesktopMainForTest(overrides = {}) {
  require('./localization.cjs').createLocalization(path.join(require('node:os').tmpdir(), 'office-boundary-test-language'), 'zh-CN')
  const ipcHandlers = new Map()
  const appHandlers = new Map()
  const electron = {
    app: {
      requestSingleInstanceLock: () => true,
      whenReady: () => ({ then: () => ({ catch: () => undefined }) }),
      on: (event, handler) => appHandlers.set(event, handler),
      quit: () => undefined,
    },
    BrowserWindow: Object.assign(function BrowserWindow() {}, { getAllWindows: () => [] }),
    dialog: {},
    shell: { showItemInFolder: overrides.showItemInFolder || (() => undefined), openExternal: overrides.openExternal || (async () => {}) },
    ipcMain: { handle: (channel, handler) => ipcHandlers.set(channel, handler) },
  }
  const sandbox = {
    AbortController,
    URL,
    clearTimeout,
    console: { log: () => undefined },
    fetch: overrides.fetch || (async () => {
      throw new Error('offline')
    }),
    process: { env: overrides.env || {}, platform: 'win32' },
    setTimeout: overrides.setTimeout || setTimeout,
    __dirname,
    require(specifier) {
      if (specifier === 'electron') return electron
      if (specifier === 'node:child_process' && overrides.spawn) return { spawn: overrides.spawn }
      if (specifier === './process-lifecycle.cjs' && overrides.stopOwnedProcess) return { stopOwnedProcess: overrides.stopOwnedProcess }
      return require(specifier)
    },
  }
  const source = `${read(mainPath)}
globalThis.__desktopMainForTest = { waitForGateway, startGateway, requestGateway, runGatewayHeartbeat, registerIpcHandlers, setServiceStatus, getServiceStatus, pingGatewayService, setMainWindow: (value) => { mainWindow = value } }
`
  new vm.Script(source, { filename: mainPath }).runInNewContext(sandbox)
  return { ...sandbox.__desktopMainForTest, ipcHandlers, appHandlers }
}

test('closing during Gateway readiness stops its owned process and never launches a fallback', async () => {
  const { EventEmitter } = require('node:events')
  const child = Object.assign(new EventEmitter(), { pid: 123, exitCode: null, stdout: null, stderr: null })
  let notifySpawn
  const spawned = new Promise(resolve => { notifySpawn = resolve })
  let spawns = 0
  const stopped = []
  const harness = loadDesktopMainForTest({
    fetch: async () => ({ ok: false }),
    spawn: () => { spawns++; notifySpawn(); return child },
    stopOwnedProcess: handle => { stopped.push(handle); handle.exitCode = 0 },
  })
  const result = harness.startGateway().then(() => null, error => error)
  await spawned
  harness.appHandlers.get('before-quit')({ preventDefault() {} })
  assert.match((await result).message, /正在退出|就绪前退出/)
  assert.deepEqual(stopped, [child])
  assert.equal(spawns, 1)
})

test('ready business requests do not probe health and never replay a failed POST', async () => {
  let healthy = true
  let probes = 0
  let requests = 0
  const harness = loadDesktopMainForTest({
    env: { DEER_FLOW_GATEWAY_URL: 'http://127.0.0.1:18888' },
    spawn: () => { throw new Error('external Gateway must not spawn a local replacement') },
    fetch: async url => {
      if (url.endsWith('/health')) {
        probes++
        return { ok: healthy, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      }
      requests++
      if (requests === 6) throw Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNREFUSED' } })
      return { ok: true, text: async () => '{"ok":true}' }
    },
  })
  await harness.startGateway()
  healthy = false
  await Promise.all(Array.from({ length: 5 }, () => harness.requestGateway('/api/office/employees')))
  assert.equal(probes, 1)
  await assert.rejects(harness.requestGateway('/api/office/messages', { method: 'POST', body: { text: '一次' } }), /fetch failed/)
  assert.equal(requests, 6)
  await assert.rejects(harness.startGateway(), /外部.*不可用/)
  assert.equal(requests, 6)
})

test('slow health never duplicates a live managed process; exited process recovers once', async () => {
  const { EventEmitter } = require('node:events')
  const children = []
  let respond = false
  const harness = loadDesktopMainForTest({
    fetch: async url => url.endsWith('/health')
      ? { ok: respond, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      : { ok: true, text: async () => '{}' },
    spawn: () => {
      const child = Object.assign(new EventEmitter(), { pid: 100 + children.length, exitCode: null })
      children.push(child)
      respond = true
      return child
    },
  })
  await Promise.all(Array.from({ length: 10 }, () => harness.startGateway()))
  assert.equal(children.length, 1)
  respond = false
  await harness.runGatewayHeartbeat()
  await harness.runGatewayHeartbeat()
  assert.equal(children.length, 1)
  await harness.requestGateway('/api/office/employees')
  children[0].exitCode = 1
  children[0].emit('exit', 1, null)
  await Promise.all(Array.from({ length: 10 }, () => harness.requestGateway('/api/office/employees')))
  assert.equal(children.length, 2)
})

test('a request timeout or HTTP error does not restart or replay the Gateway', async () => {
  let probes = 0
  let requests = 0
  const harness = loadDesktopMainForTest({
    spawn: () => { throw new Error('must not restart') },
    fetch: async (url, options) => {
      if (url.endsWith('/health')) {
        probes++
        return { ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      }
      requests++
      if (requests === 1) return { ok: false, status: 409, text: async () => '{"detail":"busy"}' }
      return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), { name: 'AbortError' }))))
    },
  })
  await assert.rejects(harness.requestGateway('/api/office/messages', { method: 'POST' }), /busy/)
  await assert.rejects(harness.requestGateway('/api/office/messages', { method: 'POST' }, 10), /请求超时/)
  assert.equal(probes, 1)
  assert.equal(requests, 2)
})


test('bootstrap uses model timeout and reports an unconfirmed outcome without replay', async () => {
  const deadlines = []
  const requests = []
  const harness = loadDesktopMainForTest({
    setTimeout: (callback, delay) => {
      deadlines.push(delay)
      return setTimeout(callback, delay === 180000 ? 10 : delay)
    },
    fetch: async (url, options) => {
      if (url.endsWith('/health')) return { ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      requests.push(url)
      return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), { name: 'AbortError' }))))
    },
  })
  harness.registerIpcHandlers()
  await assert.rejects(harness.ipcHandlers.get('office-desktop:testOfficeBootstrapModel')({}, {}), /请求超时/)
  await assert.rejects(harness.ipcHandlers.get('office-desktop:initializeOfficeBootstrap')({}, {}), /结果尚未确认/)
  assert.equal(deadlines.filter(delay => delay === 180000).length, 2)
  assert.equal(requests.length, 2)
})

test('desktop entry files are valid JavaScript', () => {
  assert.doesNotThrow(() => new vm.Script(read(mainPath), { filename: mainPath }))
  assert.doesNotThrow(() => new vm.Script(read(preloadPath), { filename: preloadPath }))
})

test('file reveal requires the main frame and a Gateway-authorized existing file', async () => {
  const shown = []
  const requests = []
  const harness = loadDesktopMainForTest({
    spawn: () => { throw new Error('File-reveal tests must never start a real Gateway') },
    showItemInFolder: (file) => shown.push(file),
    fetch: async (url, options = {}) => {
      if (url.endsWith('/health')) return { ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      requests.push({ url, body: JSON.parse(options.body) })
      return { ok: true, text: async () => JSON.stringify({ path: mainPath }) }
    },
  })
  const webContents = { mainFrame: {} }
  harness.setMainWindow({ webContents })
  harness.registerIpcHandlers()
  const reveal = harness.ipcHandlers.get('office-desktop:revealOfficeFile')
  await assert.rejects(reveal({ sender: {} }, { path: mainPath }), /不允许/)
  await assert.rejects(reveal({ sender: webContents, senderFrame: {} }, { path: mainPath }), /不允许/)
  const event = { sender: webContents, senderFrame: webContents.mainFrame }
  await assert.rejects(reveal(event, { path: '' }), /无效/)
  assert.equal(requests.length, 0)
  await reveal(event, { path: 'docs/design.md', employeeId: 'employee-one', runId: 'run-one' })
  assert.deepEqual(shown, [mainPath])
  assert.equal(requests[0].url, 'http://127.0.0.1:8001/api/office/files/resolve')
  assert.deepEqual(requests[0].body, { path: 'docs/design.md', employeeId: 'employee-one', runId: 'run-one' })
})

test('surviving client and desktop files have no dangling relative imports', () => {
  assert.deepEqual(unresolvedRelativeImports(), [])
})

test('external URLs allow HTTP/HTTPS only and require the trusted main frame', async () => {
  const opened = []
  const harness = loadDesktopMainForTest({ openExternal: async url => { opened.push(url) } })
  const webContents = { mainFrame: {} }
  harness.setMainWindow({ webContents }); harness.registerIpcHandlers()
  const open = harness.ipcHandlers.get('office-desktop:openExternalUrl')
  const event = { sender: webContents, senderFrame: webContents.mainFrame }
  await assert.rejects(open({ sender: {} }, 'https://example.com'), /不允许/)
  for (const url of ['file:///C:/Windows/notepad.exe', 'javascript:alert(1)', 'data:text/html,test', 'ms-settings:privacy', '//example.com', 'https://user:password@example.com', 'https://exam\nple.com', 'x'.repeat(2082)]) {
    await assert.rejects(open(event, url))
  }
  assert.equal(opened.length, 0)
  await open(event, 'http://localhost:8080/')
  await open(event, 'https://example.com/path?q=test')
  assert.deepEqual(opened, ['http://localhost:8080/', 'https://example.com/path?q=test'])
})

test('file preview forwards the scoped reference through the Gateway, never arbitrary local reads', async () => {
  const requests = []
  const harness = loadDesktopMainForTest({
    spawn: () => { throw new Error('test must not launch Gateway') },
    fetch: async (url, options = {}) => {
      if (url.endsWith('/health')) return { ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      requests.push({ url, body: JSON.parse(options.body) })
      return { ok: true, text: async () => JSON.stringify({ name: 'test.md', kind: 'text', content: 'test' }) }
    },
  })
  const webContents = { mainFrame: {} }
  harness.setMainWindow({ webContents }); harness.registerIpcHandlers()
  const preview = harness.ipcHandlers.get('office-desktop:previewOfficeFile')
  await assert.rejects(preview({ sender: webContents, senderFrame: {} }, { path: 'test.md' }), /不允许/)
  const reference = { path: 'test.md', employeeId: 'pm', runId: 'run-one', metadataOnly: true }
  const result = await preview({ sender: webContents, senderFrame: webContents.mainFrame }, reference)
  assert.equal(result.content, 'test')
  assert.equal(requests[0].url, 'http://127.0.0.1:8001/api/office/files/preview')
  assert.deepEqual(requests[0].body, reference)
})

test('desktop invoke channels are exactly the approved static whitelist', () => {
  const main = read(mainPath)
  const preload = read(preloadPath)

  assertStaticLiteralCalls(main, 'ipcMain.handle')
  assertStaticLiteralCalls(preload, 'ipcRenderer.invoke')
  assertDirectCallsOnly(main, 'ipcMain.handle')
  assertDirectCallsOnly(preload, 'ipcRenderer.invoke')
  assertExactMembers([...new Set(memberAccesses(main, 'ipcMain'))], ['handle'], 'ipcMain members')
  assertExactMembers(
    [...new Set(memberAccesses(preload, 'ipcRenderer'))],
    ['invoke', 'on', 'removeListener'],
    'ipcRenderer members',
  )
  assertExactMembers(literalFirstArguments(main, 'ipcMain.handle'), expectedInvokeChannels, 'main IPC handlers')
  assertExactMembers(
    literalFirstArguments(preload, 'ipcRenderer.invoke'),
    expectedInvokeChannels,
    'preload IPC invocations',
  )

  const declaredChannels = [
    ...`${main}\n${preload}`.matchAll(/['"](office-desktop:[A-Za-z][A-Za-z0-9.-]*)['"]/g),
  ].map((match) => match[1])
  assertExactMembers(
    [...new Set(declaredChannels)],
    [...expectedInvokeChannels, expectedRendererEventChannel],
    'desktop IPC channel literals',
  )
  assert.doesNotMatch(`${main}\n${preload}`, /['"]office-desktop:['"]\s*\+/)
  assert.doesNotMatch(`${main}\n${preload}`, /`office-desktop:[^`]*\$\{/)
})

test('preload bridge and TypeScript API are exactly the approved method whitelist', () => {
  const preloadMethods = [...read(preloadPath).matchAll(/^  ([A-Za-z]\w*):/gm)].map((match) => match[1])
  const types = read(path.join(clientSource, 'services', 'desktop', 'types.ts'))
  const apiBlock = types.match(/export type DesktopBridgeApi = \{([\s\S]*?)^\}/m)?.[1] || ''
  const apiMethods = [...apiBlock.matchAll(/^  ([A-Za-z]\w*):/gm)].map((match) => match[1])

  assertExactMembers(preloadMethods, expectedBridgeMethods, 'preload bridge methods')
  assertExactMembers(apiMethods, expectedBridgeMethods, 'TypeScript desktop API methods')
})

test('Gateway failures reject with their cause instead of JSON parser errors', async () => {
  for (const scenario of [
    { status: 401, body: JSON.stringify({ detail: '模型连接失败：API Key 验证未通过' }), message: '模型连接失败：API Key 验证未通过' },
    { status: 500, body: 'Internal Server Error', message: 'Gateway 返回了非 JSON 响应（HTTP 500）' },
    { status: 200, body: '<html>invalid</html>', message: 'Gateway 返回了非 JSON 响应（HTTP 200）' },
  ]) {
    let calls = 0
    const harness = loadDesktopMainForTest({
      fetch: async (url) => {
        if (url.endsWith('/health')) return { ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
        calls++
        return { ok: scenario.status < 400, status: scenario.status, text: async () => scenario.body }
      },
    })
    harness.registerIpcHandlers()
    await assert.rejects(harness.ipcHandlers.get('office-desktop:testOfficeBootstrapModel')({}, {}),
      error => error.message === scenario.message)
    assert.equal(calls, 1, 'a failed request must not be retried or treated as success')
  }
})

test('Gateway error codes use the selected language without displaying provider bodies', async () => {
  const harness = loadDesktopMainForTest({
    fetch: async url => url.endsWith('/health')
      ? { ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }
      : { ok: false, status: 401, text: async () => JSON.stringify({ detail: {
        code: 'errors.provider_auth', params: {}, message: 'provider-echoed-secret',
      } }) },
  })
  require('./localization.cjs').createLocalization(path.join(require('node:os').tmpdir(), 'office-error-test-language'), 'en')
  await assert.rejects(harness.requestGateway('/api/office/models'), error => {
    assert.match(error.message, /Authentication/)
    assert.doesNotMatch(error.message, /provider-echoed-secret/)
    return true
  })
})

test('Office IPC handlers forward only the approved Gateway requests', async () => {
  const requests = []
  const { ipcHandlers, registerIpcHandlers } = loadDesktopMainForTest({
    fetch: async (url, options = {}) => {
      if (url.endsWith('/health')) {
        return {
          ok: true,
          json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }),
        }
      }
      requests.push({
        url: new URL(url).pathname + new URL(url).search,
        method: options.method || 'GET',
        body: options.body === undefined ? undefined : JSON.parse(options.body),
      })
      return { ok: true, text: async () => '{}' }
    },
  })
  registerIpcHandlers()

  const invoke = (method, ...args) => ipcHandlers.get(`office-desktop:${method}`)({}, ...args)
  const command = {
    messageId: 'message-one',
    conversationKind: 'group',
    text: '检查项目',
    mentionEmployeeIds: ['employee-one'],
  }
  const bootstrap = {
    companyName: '星河创意工作室',
    modelName: 'mimo-v2.5',
    baseUrl: 'https://example.com/v1',
    apiKey: 'test-secret',
  }

  await invoke('getOfficeBootstrap')
  await invoke('getOfficeSettings')
  await invoke('saveOfficeSettings', { companyName: bootstrap.companyName })
  await invoke('listOfficeModels')
  const modelDraft = { requestId: 'id', provider: 'openai', modelName: 'gpt-test', baseUrl: bootstrap.baseUrl, apiKey: bootstrap.apiKey, supportsVision: false }
  await invoke('addOfficeModel', modelDraft)
  const modelEdit = { modelName: 'gpt-updated', baseUrl: bootstrap.baseUrl, apiKey: '', supportsVision: true }
  await invoke('getOfficeModel', 'model/one')
  await invoke('updateOfficeModel', 'model/one', modelEdit)
  await invoke('setOfficeDefaultModel', 'model-one')
  await invoke('getUserMemory')
  await invoke('getEmployeeMemory', 'employee/one')
  await invoke('listWorkLogDays', 'employee-one', '2026-11-18')
  await invoke('getWorkLogDay', 'employee-one', '2026-11-17')
  await invoke('createMemoryFact', { content: '偏好简洁回答', category: 'user_preference', confidence: 1 })
  await invoke('updateMemoryFact', 'fact-one', { content: '偏好简洁回答' })
  await invoke('deleteMemoryFact', 'fact-one')
  await invoke('testOfficeBootstrapModel', bootstrap)
  await invoke('initializeOfficeBootstrap', bootstrap)
  await invoke('listOfficeEmployees')
  await invoke('getOfficeEmployee', 'employee/two')
  await invoke('listOfficeEmployeeTemplates')
  await invoke('listModels')
  await invoke('listSkills')
  await invoke('createOfficeEmployee', {
    id: 'employee-two', templateId: 'frontend-engineer', displayName: '周礼',
    avatar: 'adam',
    prompt: '你是前端工程师。', skills: ['find-skills'],
  })
  await invoke('saveOfficeEmployeePersonalInfo', 'employee-two', {
    displayName: '新周礼', prompt: 'Personal prompt', skills: [],
  })
  await invoke('deleteOfficeEmployee', 'employee-two')
  await invoke('sendOfficeMessage', command)
  await invoke('listOfficeGroupMessages', 12)
  await invoke('listOfficeConversationMessages', 'employee-one', 'private')
  await invoke('listOfficeQueue')
  await invoke('editOfficeQueue', 'queue-one', '修改后的任务')
  await invoke('deleteOfficeQueue', 'queue-one')
  await invoke('cancelOfficeRun', 'employee-one', 'run-one')
  await invoke('resumeOfficeRun', 'employee-one', 'run-one', {
    source: 'ask_clarification',
    requestId: 'clarification:call-one',
    text: '继续处理',
  })
  await invoke('getOfficeRun', 'employee-one', 'run-one')
  await invoke('listOfficeRuns', 'employee-one', 'private', 7)
  await invoke('listOfficeRunEvents', 'employee-one', 'run-one', 18)

  assert.deepEqual(requests, [
    { url: '/api/office/bootstrap', method: 'GET', body: undefined },
    { url: '/api/office/settings', method: 'GET', body: undefined },
    { url: '/api/office/settings', method: 'PATCH', body: { companyName: bootstrap.companyName } },
    { url: '/api/office/models', method: 'GET', body: undefined },
    { url: '/api/office/models', method: 'POST', body: modelDraft },
    { url: '/api/office/models/model%2Fone', method: 'GET', body: undefined },
    { url: '/api/office/models/model%2Fone', method: 'PUT', body: modelEdit },
    { url: '/api/office/models/default', method: 'PUT', body: { modelId: 'model-one' } },
    { url: '/api/memory', method: 'GET', body: undefined },
    { url: '/api/office/employees/employee%2Fone/memory', method: 'GET', body: undefined },
    { url: '/api/office/employees/employee-one/work-logs?before=2026-11-18', method: 'GET', body: undefined },
    { url: '/api/office/employees/employee-one/work-logs/2026-11-17', method: 'GET', body: undefined },
    { url: '/api/memory/facts', method: 'POST', body: { content: '偏好简洁回答', category: 'user_preference', confidence: 1 } },
    { url: '/api/memory/facts/fact-one', method: 'PATCH', body: { content: '偏好简洁回答' } },
    { url: '/api/memory/facts/fact-one', method: 'DELETE', body: undefined },
    { url: '/api/office/bootstrap/test-model', method: 'POST', body: bootstrap },
    { url: '/api/office/bootstrap', method: 'POST', body: bootstrap },
    { url: '/api/office/employees', method: 'GET', body: undefined },
    { url: '/api/office/employees/employee%2Ftwo', method: 'GET', body: undefined },
    { url: '/api/office/employee-templates', method: 'GET', body: undefined },
    { url: '/api/models', method: 'GET', body: undefined },
    { url: '/api/skills', method: 'GET', body: undefined },
    {
      url: '/api/office/employees',
      method: 'POST',
      body: {
        id: 'employee-two', templateId: 'frontend-engineer', displayName: '周礼',
         avatar: 'adam',
         prompt: '你是前端工程师。', skills: ['find-skills'],
      },
    },
    {
      url: '/api/office/employees/employee-two/personal-info',
      method: 'PUT',
      body: { displayName: '新周礼', prompt: 'Personal prompt', skills: [] },
    },
    { url: '/api/office/employees/employee-two', method: 'DELETE', body: undefined },
    { url: '/api/office/messages', method: 'POST', body: command },
    { url: '/api/office/group/messages?afterSequence=12', method: 'GET', body: undefined },
    {
      url: '/api/office/employees/employee-one/conversations/private/messages?limit=500',
      method: 'GET',
      body: undefined,
    },
    { url: '/api/office/queue', method: 'GET', body: undefined },
    { url: '/api/office/queue/queue-one', method: 'PATCH', body: { text: '修改后的任务' } },
    { url: '/api/office/queue/queue-one', method: 'DELETE', body: undefined },
    {
      url: '/api/office/employees/employee-one/runs/run-one/cancel',
      method: 'POST',
      body: undefined,
    },
    {
      url: '/api/office/employees/employee-one/runs/run-one/resume',
      method: 'POST',
      body: {
        source: 'ask_clarification',
        requestId: 'clarification:call-one',
        text: '继续处理',
      },
    },
    {
      url: '/api/office/employees/employee-one/runs/run-one',
      method: 'GET',
      body: undefined,
    },
    {
      url: '/api/office/employees/employee-one/runs?conversationKind=private&limit=7',
      method: 'GET',
      body: undefined,
    },
    {
      url: '/api/office/employees/employee-one/runs/run-one/events?after_seq=18',
      method: 'GET',
      body: undefined,
    },
  ])
})

test('renderer Office API stays typed, read-only toward Run creation, and sequence based', () => {
  const apiPath = path.join(clientSource, 'services', 'office', 'officeApi.ts')
  const eventsPath = path.join(clientSource, 'services', 'office', 'officeEvents.ts')

  assert.equal(fs.existsSync(apiPath), true, 'typed Office API module is missing')
  assert.equal(fs.existsSync(eventsPath), true, 'Run Event reader module is missing')

  const api = read(apiPath)
  const events = read(eventsPath)
  assert.doesNotMatch(`${api}\n${events}`, /\bfetch\s*\(|XMLHttpRequest|axios/)
  assert.doesNotMatch(`${api}\n${events}`, /createOfficeRun|createDelegationRun|POST \/employees\/.*\/runs/)
  assert.match(api, /export type OfficeEmployeeSummary/)
  assert.match(api, /export type OfficeRunEvent/)
  assert.match(events, /afterSeq/)
  assert.match(events, /nextAfterSeq/)
  assert.doesNotMatch(events, /localStorage|sessionStorage|create\(|new Map\(|useState/)
})

test('first-run office bootstrap gates the shell without an account login flow', () => {
  const app = read(path.join(clientSource, 'App.tsx'))
  const index = read(path.join(clientSource, 'index.tsx'))
  const bootstrapPath = path.join(clientSource, 'components', 'OfficeBootstrapScreen.tsx')

  assert.equal(fs.existsSync(bootstrapPath), true, 'office bootstrap screen is missing')
  const bootstrap = read(bootstrapPath)
  assert.match(app, /HashRouter/)
  assert.match(app, /path="\/login"/)
  assert.match(app, /path="\/office"/)
  assert.match(app, /react-router-dom/)
  assert.doesNotMatch(index, /PhaserGame/)
  assert.match(app, /lazy\(/)
  assert.match(bootstrap, /ui\.create_your_office/)
  assert.match(bootstrap, /ui\.test_connection/)
  assert.match(bootstrap, /ui\.enter_office/)
  assert.match(bootstrap, /officeApi\.testBootstrapModel/)
  assert.match(bootstrap, /officeApi\.initializeBootstrap/)
  assert.doesNotMatch(bootstrap, /用户名|密码|登录账号/)
})

test('recruitment inherits the initialized default model without exposing a model picker', () => {
  const panel = read(path.join(clientSource, 'components', 'OfficeEmployeePanel.tsx'))
  const api = read(path.join(clientSource, 'services', 'office', 'officeApi.ts'))

  assert.doesNotMatch(panel, /officeApi\.listModels\(\)/)
  assert.doesNotMatch(panel, /setModelProfileId/)
  assert.doesNotMatch(panel, /modelProfileId,\n\s*prompt/)
  assert.match(panel, /officeApi\.listEmployees\(\{ fresh: true \}\)/)
  assert.match(api, /office-employees-updated/)
  const createInput = api.match(/export type OfficeCreateEmployeeInput = \{[\s\S]*?\n\}/)?.[0] || ''
  assert.doesNotMatch(createInput, /modelProfileId/)
})

test('office scene refreshes employee projection after bootstrap completes', () => {
  const bootstrap = read(path.join(clientSource, 'components', 'OfficeBootstrapScreen.tsx'))
  const game = read(path.join(clientSource, 'scenes', 'Game.ts'))

  assert.match(bootstrap, /officeApi\.listEmployees\(\{ fresh: true \}\)/)
  assert.match(game, /officeApi\.listEmployees\(\)/)
  assert.match(game, /this\.syncOfficeEmployees\(employees\)/)
})

test('private chat is entered from the conversation list, not the office map', () => {
  const employee = read(path.join(clientSource, 'characters', 'OfficeEmployee.ts'))

  assert.doesNotMatch(employee, /office-employee-selected/)
})

test('desktop IPC allows one outbound event channel and no alternate renderer transport', () => {
  const main = read(mainPath)
  const preload = read(preloadPath)

  for (const forbiddenMainCall of ['ipcMain.on', 'ipcMain.once', 'ipcMain.handleOnce']) {
    assert.equal(countCalls(main, forbiddenMainCall), 0, `${forbiddenMainCall} is not part of the desktop contract`)
  }
  for (const forbiddenRendererCall of [
    'ipcRenderer.send',
    'ipcRenderer.sendSync',
    'ipcRenderer.postMessage',
    'ipcRenderer.once',
  ]) {
    assert.equal(
      countCalls(preload, forbiddenRendererCall),
      0,
      `${forbiddenRendererCall} is not part of the desktop contract`,
    )
  }

  assertStaticLiteralCalls(main, 'webContents.send')
  assertStaticLiteralCalls(preload, 'ipcRenderer.on')
  assertStaticLiteralCalls(preload, 'ipcRenderer.removeListener')
  assertDirectCallsOnly(main, 'webContents.send')
  assertDirectCallsOnly(preload, 'ipcRenderer.on')
  assertDirectCallsOnly(preload, 'ipcRenderer.removeListener')
  assertExactMembers(
    literalFirstArguments(main, 'webContents.send'),
    [expectedRendererEventChannel],
    'main renderer event channels',
  )
  assertExactMembers(
    literalFirstArguments(preload, 'ipcRenderer.on'),
    [expectedRendererEventChannel],
    'preload renderer event listeners',
  )
  assertExactMembers(
    literalFirstArguments(preload, 'ipcRenderer.removeListener'),
    [expectedRendererEventChannel],
    'preload renderer event cleanup',
  )

  assertStaticLiteralCalls(preload, 'contextBridge.exposeInMainWorld')
  assertDirectCallsOnly(preload, 'contextBridge.exposeInMainWorld')
  assertExactMembers(
    [...new Set(memberAccesses(preload, 'contextBridge'))],
    ['exposeInMainWorld'],
    'contextBridge members',
  )
  assertExactMembers(
    literalFirstArguments(preload, 'contextBridge.exposeInMainWorld'),
    ['officeDesktopBridge'],
    'preload global bridge names',
  )
  assert.doesNotMatch(`${main}\n${preload}`, /ipc(?:Main|Renderer)\s*\[/)
  assert.doesNotMatch(
    `${main}\n${preload}`,
    /\b(?:MessageChannelMain|MessagePortMain)\b|webContents\.postMessage\s*\(/,
  )
})

test('desktop bridge event types are exactly the approved projection whitelist', () => {
  const types = read(path.join(clientSource, 'services', 'desktop', 'types.ts'))
  const eventBlock = types.match(
    /export type DesktopBridgeEvent =([\s\S]*?)\r?\n\r?\nexport type DesktopBridgeApi/,
  )?.[1] || ''
  const eventTypes = [...eventBlock.matchAll(/type:\s*'([^']+)'/g)].map((match) => match[1])
  const emittedEventTypes = [...read(mainPath).matchAll(/broadcastEvent\(\{\s*type:\s*'([^']+)'/g)].map(
    (match) => match[1],
  )

  assert.equal(
    emittedEventTypes.length,
    countCalls(read(mainPath), 'broadcastEvent') - 1,
    'every main-process event emission must use an inline static event type',
  )
  assertExactMembers(eventTypes, expectedBridgeEventTypes, 'TypeScript desktop bridge event types')
  assertExactMembers(
    [...new Set(emittedEventTypes)],
    expectedBridgeEventTypes,
    'main desktop bridge event types',
  )
})

test('Electron IPC primitives are confined to the main and preload boundaries', () => {
  const allowedFiles = new Set([mainPath, preloadPath].map((file) => path.resolve(file)))
  const activeFiles = [
    ...walk(clientSource),
    ...walk(__dirname).filter(
      (file) => !file.endsWith('.test.cjs') && !file.endsWith('-smoke.cjs'),
    ),
  ].filter((file) => ['.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs'].includes(path.extname(file)))

  for (const file of activeFiles) {
    if (allowedFiles.has(path.resolve(file))) continue
    assert.doesNotMatch(
      read(file),
      /\b(?:ipcMain|ipcRenderer|contextBridge)\b|webContents\.send\s*\(|office-desktop:/,
      `Electron IPC escaped the approved boundary: ${path.relative(root, file)}`,
    )
  }
})

test('repository snapshot retains current docs and minimum verification entrypoints', () => {
  const requiredSnapshotPaths = [
    'AGENTS.md',
    'CONTEXT.md',
    'ARCHITECTURE.md',
    'backend/UPSTREAM.md',
    'backend/docs/SETUP.md',
    'desktop/preload-sandbox-smoke.cjs',
    'desktop/client-render-smoke.cjs',
    'client/src/components/DesktopGatewayBadge.tsx',
    'client/src/state/desktopGatewayStore.ts',
  ]
  for (const requiredPath of requiredSnapshotPaths) {
    assert.equal(
      fs.existsSync(path.join(root, requiredPath)),
      true,
      `required snapshot path is missing: ${requiredPath}`,
    )
  }

  const rootPackage = JSON.parse(read(path.join(root, 'package.json')))
  assert.equal(
    rootPackage.scripts?.['test:minimum'],
    'npm run test:desktop-boundary && npm run test:desktop-client',
  )
  assert.equal(
    rootPackage.scripts?.['test:desktop-client'],
    'npm run desktop:client:build && electron desktop/client-render-smoke.cjs && electron desktop/client-render-smoke.cjs --refresh-isolation',
  )

  const readme = read(path.join(root, 'README.md'))
  assert.match(readme, /npm run test:minimum/)

  const gitignore = read(path.join(root, '.gitignore'))
  assert.match(gitignore, /^\/output\/$/m)
})

test('the minimal shell keeps current independent desktop capabilities mounted', () => {
  const shell = read(path.join(clientSource, 'OfficeShell.tsx'))
  for (const component of ['DesktopZoomController', 'DesktopGatewayBadge', 'WorkspacePanel', 'AppToast']) {
    assert.match(shell, new RegExp(`<${component}\\s*/>`))
  }
})

test('desktop renderer keeps the Electron sandbox enabled', () => {
  const main = read(mainPath)
  assert.match(main, /sandbox:\s*true/)
  assert.doesNotMatch(main, /sandbox:\s*false/)
})

test('desktop entry text has no replacement question-mark runs', () => {
  assert.doesNotMatch(read(mainPath), /\?{3,}/)
})

test('gateway startup observes child-process spawn errors', async () => {
  const { EventEmitter } = require('node:events')
  const { waitForGateway } = loadDesktopMainForTest()
  const processHandle = new EventEmitter()
  processHandle.exitCode = null

  const resultPromise = waitForGateway(processHandle, 500).then(
    () => undefined,
    (error) => error,
  )

  if (processHandle.listenerCount('error') === 0) {
    processHandle.exitCode = 1
    await resultPromise
    assert.fail('waitForGateway must subscribe to child-process error events')
  }

  processHandle.emit('error', new Error('spawn ENOENT'))
  const error = await resultPromise
  assert.equal(typeof error?.message, 'string')
  assert.match(error.message, /spawn ENOENT/)
  assert.equal(processHandle.listenerCount('error'), 0)
})


test('gateway startup can retry after every candidate fails', async () => {
  const { EventEmitter } = require('node:events')
  let spawnCount = 0
  const { startGateway } = loadDesktopMainForTest({
    spawn: () => {
      spawnCount += 1
      const processHandle = new EventEmitter()
      processHandle.exitCode = null
      processHandle.stdout = null
      processHandle.stderr = null
      processHandle.kill = () => {
        processHandle.exitCode = 1
      }
      setTimeout(() => processHandle.emit('error', new Error('spawn failed')), 0)
      return processHandle
    },
  })

  await assert.rejects(startGateway(), /spawn failed/)
  const firstAttemptCount = spawnCount
  await assert.rejects(startGateway(), /spawn failed/)
  assert.ok(spawnCount > firstAttemptCount, 'a rejected startup promise must not block retries')
})


test('Gateway status transitions discard stale details and preserve current diagnostics', async () => {
  const { setServiceStatus, getServiceStatus, pingGatewayService } = loadDesktopMainForTest({
    fetch: async () => ({ ok: true, json: async () => ({ status: 'healthy', service: 'deer-flow-gateway' }) }),
    spawn: () => { throw new Error('Status tests must never start a real Gateway') },
  })
  const details = Object.fromEntries(Object.entries(JSON.parse(read(gatewayStatusContractPath))).map(([key, value]) => [key, require('../contracts/locales/zh-CN.json')[value]]))
  await pingGatewayService()
  assert.equal(getServiceStatus().detail, details.connected)
  setServiceStatus({ lifecycle: 'error', detail: 'connection refused' })
  setServiceStatus({ transport: 'http' })
  assert.equal(getServiceStatus().detail, 'connection refused')
  await pingGatewayService()
  assert.equal(getServiceStatus().detail, details.connected)
  setServiceStatus({ lifecycle: 'starting', detail: details.launching })
  setServiceStatus({ lifecycle: 'starting' })
  assert.equal(getServiceStatus().detail, details.launching)
})

test('Gateway default status copy comes from one shared contract', () => {
  const details = JSON.parse(read(gatewayStatusContractPath))
  const main = read(mainPath)
  const desktopBridge = read(path.join(clientSource, 'services', 'desktop', 'desktopBridge.ts'))
  const gatewayStore = read(path.join(clientSource, 'state', 'desktopGatewayStore.ts'))
  const gatewayDetailHelper = read(path.join(clientSource, 'services', 'desktop', 'gatewayStatusDetails.ts'))

  for (const lifecycle of ['browser', 'starting', 'connected', 'unavailable', 'error']) {
    assert.equal(typeof details[lifecycle], 'string', `missing shared copy for ${lifecycle}`)
    assert.ok(details[lifecycle].length > 0, `empty shared copy for ${lifecycle}`)
  }

  assert.match(main, /getGatewayStatusDetail\(lifecycle\)/)
  assert.match(desktopBridge, /normalizeGatewayStatus/)
  assert.match(desktopBridge, /createGatewayStatus/)
  assert.match(gatewayStore, /createGatewayStatus/)
  assert.match(gatewayDetailHelper, /desktop-gateway-status\.json/)

  for (const detail of Object.values(details)) {
    for (const [label, source] of [
      ['main.cjs', main],
      ['desktopBridge.ts', desktopBridge],
      ['desktopGatewayStore.ts', gatewayStore],
    ]) {
      for (const quote of ["'", '"', '`']) {
        assert.equal(source.includes(`${quote}${detail}${quote}`), false, `${label} duplicates shared status copy: ${detail}`)
      }
    }
  }
})

test('workspace projection never stores renderer-owned WorkspaceSummary authority', () => {
  const bridgeSource = read(path.join(clientSource, 'services', 'desktop', 'workspaceBridge.ts'))
  const storeSource = read(path.join(clientSource, 'state', 'workspaceStore.ts'))
  const panelSource = read(path.join(clientSource, 'components', 'WorkspacePanel.tsx'))

  const subscribeIndex = bridgeSource.indexOf('unsubscribe = bridge.onServiceEvent')
  const initialRefreshIndex = bridgeSource.indexOf('void refreshWorkspaceSummary().catch', subscribeIndex)
  const cancelStart = bridgeSource.indexOf('if (result.canceled)')
  const cancelEnd = bridgeSource.indexOf('const workspace = await refreshWorkspaceSummary()', cancelStart)
  const cancelBlock = bridgeSource.slice(cancelStart, cancelEnd)
  const workspaceEventStart = bridgeSource.indexOf("if (event.type === 'workspace.updated')")
  const workspaceEventEnd = bridgeSource.indexOf('const previousLifecycle', workspaceEventStart)
  const workspaceEventBlock = bridgeSource.slice(workspaceEventStart, workspaceEventEnd)

  assert.ok(subscribeIndex >= 0)
  assert.ok(initialRefreshIndex >= 0)
  assert.ok(subscribeIndex < initialRefreshIndex, 'workspace listener must be installed before the first authoritative refresh')
  assert.match(cancelBlock, /await refreshWorkspaceSummary\(\)/)
  assert.doesNotMatch(cancelBlock, /previousState|setSummary\(|result\.workspace/)
  assert.match(workspaceEventBlock, /refreshWorkspaceSummary\(\)/)
  assert.doesNotMatch(workspaceEventBlock, /applyAuthoritativeWorkspace\(event\.payload\)|setSummary\(event\.payload/)
  const typesSource = read(path.join(clientSource, 'services', 'desktop', 'types.ts'))
  const mainSource = read(mainPath)
  assert.doesNotMatch(typesSource, /workspace\.updated'; payload: WorkspaceSummary/)
  assert.doesNotMatch(mainSource, /workspace\.updated', payload:/)
  assert.doesNotMatch(storeSource, /WorkspaceSummary|summary|setSummary/)
  assert.match(panelSource, /useState<WorkspaceSummary \| undefined>/)
  assert.match(panelSource, /refreshWorkspaceSummary\(\)/)
  assert.match(bridgeSource, /event\.payload\.lifecycle === 'connected'/)
})


test('library IPC rejects arbitrary routes, paths and foreign frames', async () => {
  const harness=loadDesktopMainForTest()
  const webContents={mainFrame:{}}
  harness.setMainWindow({webContents}); harness.registerIpcHandlers()
  const invoke=harness.ipcHandlers.get('office-desktop:library')
  const event={sender:webContents,senderFrame:webContents.mainFrame}
  await assert.rejects(invoke({sender:{}},{action:'list'}),/不允许/)
  await assert.rejects(invoke(event,{action:'http',route:'/api/runs'}),/无效/)
  await assert.rejects(invoke(event,{action:'rename',id:'a'.repeat(32),body:{name:'新名称'}}),/无效/)
  await assert.rejects(invoke(event,{action:'file',id:'../../secret'}),/无效/)
  await assert.rejects(invoke(event,{action:'upload',body:{filename:'a.txt'}}),/无效/)
})
