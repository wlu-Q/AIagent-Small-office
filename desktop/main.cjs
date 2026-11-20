const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron')
const gatewayStatusDetails = require('../contracts/desktop-gateway-status.json')
const { spawn, execFile } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const { backendRoot, gatewayEnv, packagedPython, packagedResources, runtimeHome } = require('./gateway-env.cjs')
const { stopOwnedProcess } = require('./process-lifecycle.cjs')
const { developmentPython } = require('./runtime-paths.cjs')
const { createLocalization, t } = require('./localization.cjs')
let localization

const gatewayEndpoint = new URL(process.env.DEER_FLOW_GATEWAY_URL || `http://127.0.0.1:${process.env.OFFICE_MANAGED_GATEWAY_PORT || '8001'}`)
if (gatewayEndpoint.protocol !== 'http:') {
  throw new Error('DEER_FLOW_GATEWAY_URL only supports HTTP URLs')
}

const gatewayUrl = gatewayEndpoint.origin
const gatewayHost = gatewayEndpoint.hostname
const gatewayPort = gatewayEndpoint.port || '80'
const usesExternalGateway = Boolean(process.env.DEER_FLOW_GATEWAY_URL)

const DEFAULT_ZOOM_FACTOR = 1
const MIN_ZOOM_FACTOR = 0.7
const MAX_ZOOM_FACTOR = 1.8

// 网关的 stdout/stderr 原先只进 Electron 控制台，进程一退出就什么都不剩，
// 「网关为什么死的」在事后无从查起。这里额外落一份盘：按大小单份轮转，
// 并且把 spawn/exit/退出码/是否 App 主动退出都记成 lifecycle 行——
// 只靠 stderr 分不清「自己死的」还是「被 App 关的」。
const gatewayLogPath = path.join(runtimeHome, 'logs', 'gateway.log')
const GATEWAY_LOG_MAX_BYTES = 5 * 1024 * 1024

// Windows 默认的 Proactor 循环有一条 asyncio 自身的崩溃路径：IOCP 完成回调打到
// 已取消的 Future 上会抛 InvalidStateError，异常逃出 run_until_complete 直接杀掉
// 进程——2026-09-16 网关猝死即此。Selector 循环不走 IOCP，这条路不存在。
// uvicorn 0.46 是自己造循环的（asyncio_run(..., loop_factory=...)），全局 event
// loop policy 被绕过，所以只能从命令行换。代价：Windows 的 selector 循环不支持
// asyncio 子进程，而本项目没用到（bash 走的是线程里的同步 subprocess）。
const GATEWAY_LOOP_ARGS = ['--loop', 'asyncio:SelectorEventLoop']

let mainWindow = null
let gatewayProcess = null
let gatewayStartPromise = null
let gatewayReady = false
let isAppQuitting = false
let developmentRenderer = null
let developmentRendererClosed = false
// 启动器（desktop/dev.cjs）用这个退出码区分「已经有一个在跑」和「启动失败」。
// 原来是 app.quit() 静默退 0，黑窗口一闪就没了，用户只能看到「双击没反应」。
const ALREADY_RUNNING_EXIT_CODE = 42

const ownsInstance = app.requestSingleInstanceLock()
if (!ownsInstance) {
  // 这里还没 ready、也没有窗口，弹不了自家界面，只能给启动器留一句话和一个明确的退出码。
  try {
    fs.writeSync(1, '[office] Small Office is already running; switching to the existing window.\n')
  } catch {}
  process.exit(ALREADY_RUNNING_EXIT_CODE)
}
app.on('second-instance', () => {
  // 退出流程中不要重建窗口：那会造出一个正在被关闭的 App 里的孤儿窗口。
  if (isAppQuitting) return
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

function getGatewayStatusDetail(lifecycle) {
  return t(gatewayStatusDetails[lifecycle] || gatewayStatusDetails.error)
}

function createServiceStatus(patch) {
  return {
    mode: patch.mode || 'electron',
    lifecycle: patch.lifecycle,
    detail: patch.detail || getGatewayStatusDetail(patch.lifecycle),
    transport: patch.transport || 'http',
    lastUpdatedAt: Date.now(),
  }
}

const serviceStatus = createServiceStatus({
  lifecycle: 'starting',
})

function safeLog(...args) {
  if (isAppQuitting) return
  try {
    console.log(...args)
  } catch {}
}

let gatewayLogStream = null

function writeGatewayLog(line) {
  if (gatewayLogStream === null) return
  try {
    gatewayLogStream.write(`${new Date().toISOString()} ${line}\n`)
  } catch {}
}

// 生命周期事实直写文件：safeLog 在退出期间是静音的，而退出恰恰是最需要记下来的时刻。
function logGatewayEvent(event, detail = '') {
  const line = `[lifecycle] ${event}${detail ? ` ${detail}` : ''}`
  safeLog(`[deerflow-gateway] ${line}`)
  writeGatewayLog(line)
}

function logGatewayStream(prefix, detail) {
  safeLog(prefix, detail)
  writeGatewayLog(`${prefix} ${detail}`)
}

function openGatewayLog() {
  try {
    fs.mkdirSync(path.dirname(gatewayLogPath), { recursive: true })
    try {
      if (fs.statSync(gatewayLogPath).size > GATEWAY_LOG_MAX_BYTES) {
        fs.renameSync(gatewayLogPath, `${gatewayLogPath}.1`)
      }
    } catch {}
    gatewayLogStream = fs.createWriteStream(gatewayLogPath, { flags: 'a' })
    gatewayLogStream.on('error', () => {
      gatewayLogStream = null
    })
  } catch {
    // 日志写不了不能拖垮 App。
    gatewayLogStream = null
  }
}

function getServiceStatus() {
  return { ...serviceStatus }
}

function broadcastEvent(event) {
  if (isAppQuitting) return
  BrowserWindow.getAllWindows().forEach((window) => {
    if (!window.isDestroyed()) window.webContents.send('office-desktop:event', event)
  })
}

function setServiceStatus(patch) {
  const lifecycleChanged = patch.lifecycle !== undefined && patch.lifecycle !== serviceStatus.lifecycle
  const next = createServiceStatus({ ...serviceStatus, ...patch,
    detail: patch.detail ?? (lifecycleChanged ? undefined : serviceStatus.detail) })
  // lastUpdatedAt 每调一次都会变，不能拿整个对象比。心跳会周期性地把同一个
  // 状态写回来，没有实质变化就不广播，免得渲染进程每 30 秒收一次重复事件。
  const unchanged = next.lifecycle === serviceStatus.lifecycle
    && next.detail === serviceStatus.detail
    && next.transport === serviceStatus.transport
    && next.mode === serviceStatus.mode
  if (unchanged) return
  Object.assign(serviceStatus, next)
  safeLog('[desktop-service-status]', JSON.stringify(getServiceStatus()))
  broadcastEvent({ type: 'service.status', payload: getServiceStatus() })
}

function resolveRenderer() {
  if (process.env.OFFICE_RENDERER_URL) {
    return { kind: 'url', value: process.env.OFFICE_RENDERER_URL }
  }
  const builtIndex = path.join(__dirname, '..', 'client', 'dist', 'index.html')
  if (fs.existsSync(builtIndex)) return { kind: 'file', value: builtIndex }
  return {
    kind: 'inline',
    value: `<!doctype html><html lang="en"><body style="font-family:sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0"><div style="max-width:680px;padding:32px;line-height:1.7"><h2>Client has not been built</h2><p>Run <code>npm run desktop:client:build</code>, or set <code>OFFICE_RENDERER_URL</code>.</p></div></body></html>`,
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1180,
    minHeight: 760,
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  mainWindow.webContents.setZoomFactor(DEFAULT_ZOOM_FACTOR)
  // Chat links are opened only by the validated main-process IPC below.
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', event => event.preventDefault())
  // 启动器（dev.cjs --detach）用 windowsHide 拉起 Electron，窗口的**第一次** ShowWindow
  // 会被 Windows 用启动信息里的 SW_HIDE 覆盖——于是进程、渲染、网关全都正常，桌面上却
  // 看不到窗口。Windows 只对第一次显示生效，这里在页面画好后再显式 show 一次即可绕开。
  mainWindow.once('ready-to-show', () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.show()
  })
  const renderer = resolveRenderer()
  if (renderer.kind === 'url') mainWindow.loadURL(renderer.value)
  else if (renderer.kind === 'file') mainWindow.loadFile(renderer.value)
  else mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(renderer.value)}`)
}

function gatewayCandidates() {
  const args = ['-m', 'uvicorn', 'app.gateway.app:app', '--host', gatewayHost, '--port', gatewayPort, ...GATEWAY_LOOP_ARGS]
  if (packagedPython) return [{ command: packagedPython, args, label: 'bundled Python' }]
  const candidates = []
  const venvPython = developmentPython(backendRoot, process.platform)
  if (fs.existsSync(venvPython)) candidates.push({ command: venvPython, args, label: 'backend venv' })
  candidates.push({
    command: 'uv',
    args: ['run', '--no-editable', 'uvicorn', 'app.gateway.app:app', '--host', gatewayHost, '--port', gatewayPort, ...GATEWAY_LOOP_ARGS],
    label: 'uv',
  })
  return candidates
}

async function probeGateway(timeoutMs = 3000) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${gatewayUrl}/health`, { signal: controller.signal })
    if (!response.ok) return false
    const health = await response.json()
    return health?.status === 'healthy' && health?.service === 'deer-flow-gateway'
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

async function waitForGateway(processHandle, timeoutMs = 20000) {
  let spawnError
  const onSpawnError = (error) => {
    spawnError = error
  }
  processHandle.once('error', onSpawnError)

  try {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      if (isAppQuitting) throw new Error(t('desktop.the_app_is_quitting_gateway_startup_stopped'))
      if (spawnError) {
        throw new Error(t('desktop.deerflow_gateway_failed_to_start_value', { value1: (spawnError.message) }))
      }
      if (processHandle.exitCode !== null) {
        throw new Error(t('desktop.deerflow_gateway_exited_before_readiness_code_value', { value1: (processHandle.exitCode) }))
      }
      if (await probeGateway()) return
      if (spawnError) {
        throw new Error(t('desktop.deerflow_gateway_failed_to_start_value', { value1: (spawnError.message) }))
      }
      await new Promise((resolve) => setTimeout(resolve, 200))
    }
    throw new Error(t('desktop.deerflow_gateway_startup_timed_out'))
  } finally {
    processHandle.removeListener('error', onSpawnError)
  }
}

function bindGatewayProcess(processHandle, label) {
  gatewayProcess = processHandle
  logGatewayEvent('spawn', `${label} pid=${processHandle.pid}`)
  processHandle.stdout?.on('data', (chunk) => {
    for (const line of chunk.toString().split('\n')) {
      if (line.trim()) logGatewayStream('[deerflow-gateway]', line.trim())
    }
  })
  processHandle.stderr?.on('data', (chunk) => {
    // uvicorn 的裸 stderr 和 Python traceback 都从这里出来，是查死因的主要来源。
    for (const line of chunk.toString().split('\n')) {
      if (line.trim()) logGatewayStream('[deerflow-gateway:stderr]', line.trim())
    }
  })
  processHandle.on('error', (error) => {
    if (gatewayProcess === processHandle) {
      gatewayProcess = null
      gatewayReady = false
    }
    if (!isAppQuitting) {
      setServiceStatus({ lifecycle: 'error', detail: t('desktop.deerflow_gateway_process_error_value', { value1: (error.message) }) })
    }
    logGatewayEvent('process-error', `${label} pid=${processHandle.pid} ${error.message}`)
  })
  processHandle.on('exit', (code, signal) => {
    if (gatewayProcess === processHandle) {
      gatewayProcess = null
      gatewayReady = false
    }
    // quitting=true 说明是 App 主动关的；false 说明它自己没的——这条区分是排查的起点。
    logGatewayEvent('exit', `${label} pid=${processHandle.pid} code=${code ?? 'unknown'} signal=${signal ?? 'none'} quitting=${isAppQuitting}`)
    if (!isAppQuitting) {
      setServiceStatus({
        lifecycle: code === 0 ? 'unavailable' : 'error',
        detail: t('desktop.deerflow_gateway_process_exited_value_code_value', { value1: (label), value2: (code ?? 'unknown') }),
      })
    }
  })
}

async function startManagedGateway() {
  let lastError
  for (const candidate of gatewayCandidates()) {
    logGatewayEvent('launching', candidate.label)
    const processHandle = spawn(candidate.command, candidate.args, {
      cwd: backendRoot,
      env: gatewayEnv(gatewayHost, gatewayPort),
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      detached: process.platform === 'darwin',
    })
    // Record ownership immediately: quitting during readiness must stop this tree too.
    bindGatewayProcess(processHandle, candidate.label)
    try {
      await waitForGateway(processHandle)
      return
    } catch (error) {
      lastError = error
      logGatewayEvent('launch-failed', `${candidate.label} ${error.message}`)
      if (processHandle.pid && processHandle.exitCode === null) {
        stopOwnedProcess(processHandle, { processGroup: process.platform === 'darwin' })
      }
      if (isAppQuitting) throw error
    }
  }
  throw lastError || new Error(t('desktop.no_available_method_to_start_deerflow_gateway'))
}

async function startGateway() {
  if (isAppQuitting) throw new Error(t('desktop.the_app_is_quitting'))
  if (!gatewayStartPromise) {
    gatewayStartPromise = (async () => {
      if (!(await probeGateway())) {
        logGatewayEvent('probe-failed', gatewayUrl)
        if (usesExternalGateway) throw new Error(t('desktop.external_deerflow_gateway_unavailable_value', { value1: (gatewayUrl) }))
        if (gatewayProcess && gatewayProcess.exitCode === null) throw new Error(t('desktop.deerflow_gateway_is_running_but_not_responding'))
        setServiceStatus({ lifecycle: 'starting', detail: t(gatewayStatusDetails.launching), transport: 'http' })
        await startManagedGateway()
      }
      gatewayReady = true
    })().finally(() => {
      gatewayStartPromise = null
    })
  }
  return gatewayStartPromise
}

async function pingGatewayService() {
  await startGateway()
  setServiceStatus({ lifecycle: 'connected', transport: 'http' })
  return { pong: true, timestamp: Date.now() }
}

const GATEWAY_HEARTBEAT_INTERVAL_MS = 30000
const GATEWAY_HEARTBEAT_PROBE_TIMEOUT_MS = 5000
const GATEWAY_HEARTBEAT_MISS_LIMIT = 2

let gatewayHeartbeatTimer = null
let gatewayHeartbeatInFlight = false
let gatewayHeartbeatMisses = 0

async function runGatewayHeartbeat() {
  if (isAppQuitting || gatewayHeartbeatInFlight) return
  gatewayHeartbeatInFlight = true
  try {
    if (await probeGateway(GATEWAY_HEARTBEAT_PROBE_TIMEOUT_MS)) {
      gatewayHeartbeatMisses = 0
      gatewayReady = true
      setServiceStatus({ lifecycle: 'connected', transport: 'http' })
      return
    }
    gatewayHeartbeatMisses += 1
    logGatewayEvent('heartbeat-miss', `${gatewayHeartbeatMisses}/${GATEWAY_HEARTBEAT_MISS_LIMIT} ${gatewayUrl}`)
    if (gatewayHeartbeatMisses < GATEWAY_HEARTBEAT_MISS_LIMIT) return
    gatewayHeartbeatMisses = 0
    await pingGatewayService()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    logGatewayEvent('heartbeat-failed', message)
    setServiceStatus({ lifecycle: 'error', detail: message })
  } finally {
    gatewayHeartbeatInFlight = false
  }
}

function startGatewayHeartbeat() {
  if (gatewayHeartbeatTimer) return
  gatewayHeartbeatTimer = setInterval(runGatewayHeartbeat, GATEWAY_HEARTBEAT_INTERVAL_MS)
  // 心跳不该拖住 App 退出。
  gatewayHeartbeatTimer.unref?.()
}

function stopGatewayHeartbeat() {
  if (!gatewayHeartbeatTimer) return
  clearInterval(gatewayHeartbeatTimer)
  gatewayHeartbeatTimer = null
}

async function requestGateway(route, { method = 'GET', body } = {}, timeoutMs = 10000) {
  if (isAppQuitting) throw new Error(t('desktop.the_app_is_quitting'))
  if (!gatewayReady) await startGateway()
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${gatewayUrl}${route}`, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    const text = await response.text()
    let payload
    try {
      payload = text ? JSON.parse(text) : undefined
    } catch {
      throw new Error(t('desktop.gateway_returned_a_non_json_response_http_value', { value1: (response.status) }))
    }
    if (!response.ok) {
      const detail = payload?.detail
      if (response.status === 409 && route === '/api/office/library/documents' && detail?.code === 'duplicate') return { conflict: detail.document }
      if (detail?.code && detail.code.startsWith('errors.')) throw new Error(t(detail.code, detail.params || {}))
      throw new Error(typeof detail === 'string' ? detail : t('desktop.gateway_request_failed_http_value', { value1: (response.status) }))
    }
    return payload
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error(t(route === '/api/office/bootstrap'
      ? 'desktop.bootstrap_result_unconfirmed' : 'desktop.gateway_request_timed_out_value', { value1: route }))
    if (error?.cause?.code === 'ECONNREFUSED') {
      gatewayReady = false
      if (!usesExternalGateway && !gatewayProcess) {
        void startGateway().catch(failure => {
          logGatewayEvent('recovery-failed', failure.message)
          setServiceStatus({ lifecycle: 'error', detail: failure.message })
        })
      }
    }
    throw error
  } finally {
    clearTimeout(timeout)
  }
}

const MODEL_SETTINGS_REQUEST_TIMEOUT_MS = 180000

function clampZoomFactor(value) {
  if (!Number.isFinite(value)) return DEFAULT_ZOOM_FACTOR
  return Math.min(MAX_ZOOM_FACTOR, Math.max(MIN_ZOOM_FACTOR, value))
}

function getZoomFactor() {
  if (!mainWindow || mainWindow.isDestroyed()) return DEFAULT_ZOOM_FACTOR
  return mainWindow.webContents.getZoomFactor()
}

function setZoomFactor(value) {
  const next = clampZoomFactor(value)
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.setZoomFactor(next)
  return next
}

function requireMainFrame(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error(t('desktop.this_window_cannot_access_files_or_open_links'))
  }
}

function registerIpcHandlers() {
  ipcMain.handle('office-desktop:openExternalUrl', async (event, value) => {
    requireMainFrame(event)
    if (typeof value !== 'string' || value.length > 2081 || /[\x00-\x20\x7f]/.test(value)) {
      throw new Error(t('desktop.invalid_url'))
    }
    let url
    try { url = new URL(value) } catch { throw new Error(t('desktop.invalid_url')) }
    if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
      throw new Error(t('desktop.only_http_https_urls_without_credentials_are_allowed'))
    }
    await shell.openExternal(url.href)
  })
  ipcMain.handle('office-desktop:previewOfficeFile', async (event, payload) => {
    requireMainFrame(event)
    return requestGateway('/api/office/files/preview', { method: 'POST', body: payload })
  })
  ipcMain.handle('office-desktop:revealOfficeFile', async (event, payload) => {
    requireMainFrame(event)
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(gatewayHost)) {
      throw new Error(t('desktop.cannot_reveal_remote_service_files_on_this_computer'))
    }
    if (!payload || typeof payload.path !== 'string' || !payload.path.trim() || payload.path.length > 4096) {
      throw new Error(t('desktop.invalid_file_path'))
    }
    const result = await requestGateway('/api/office/files/resolve', {
      method: 'POST', body: { path: payload.path, employeeId: payload.employeeId, runId: payload.runId, projectId: payload.projectId },
    })
    if (!result || !path.isAbsolute(result.path) || !fs.statSync(result.path).isFile()) {
      throw new Error(t('desktop.file_does_not_exist_or_is_not_a_regular_file'))
    }
    shell.showItemInFolder(result.path)
    return { path: result.path }
  })
  ipcMain.handle('office-desktop:getServiceStatus', async () => getServiceStatus())
  ipcMain.handle('office-desktop:pingService', async () => pingGatewayService())
  ipcMain.handle('office-desktop:getWorkspaceSummary', async () => requestGateway('/api/office/project'))
  ipcMain.handle('office-desktop:selectAndBindWorkspace', async () => {
    const selection = await dialog.showOpenDialog(mainWindow, {
      title: t('desktop.select_project_folder'),
      properties: ['openDirectory'],
    })
    const selectedPath = selection.filePaths?.[0]
    if (selection.canceled || !selectedPath) return { canceled: true }
    const result = await requestGateway('/api/office/project', {
      method: 'PUT',
      body: { path: selectedPath },
    }, 20000)
    if (result?.workspace) broadcastEvent({ type: 'workspace.updated' })
    return { canceled: false, workspace: result.workspace }
  })
  ipcMain.handle('office-desktop:listOfficeEmployees', async () => (
    requestGateway('/api/office/employees')
  ))
  ipcMain.handle('office-desktop:listOfficeEmployeeTemplates', async () => (
    requestGateway('/api/office/employee-templates')
  ))
  ipcMain.handle('office-desktop:listModels', async () => requestGateway('/api/models'))
  ipcMain.handle('office-desktop:library', async (event, payload) => {
    requireMainFrame(event)
    const actions = {
      list: ['GET', '/documents'], upload: ['POST', '/documents'],
      settings: ['GET', '/settings'], saveSettings: ['PUT', '/settings'], test: ['POST', '/test-connection'],
      file: ['GET', '/documents/:id/file'], delete: ['DELETE', '/documents/:id'],
      retry: ['POST', '/documents/:id/retry'],
    }
    if (!payload || !Object.hasOwn(actions, payload.action)) throw new Error(t('desktop.invalid_library_operation'))
    const [method, suffix] = actions[payload.action]
    if (suffix.includes(':id') && !/^[a-f0-9]{32}$/.test(payload.id || '')) throw new Error(t('desktop.invalid_document_id'))
    if (payload.version && !/^[a-f0-9]{32}$/.test(payload.version)) throw new Error(t('desktop.invalid_document_version'))
    let route = '/api/office/library' + suffix.replace(':id', payload.id || '')
    if (payload.action === 'file' && payload.version) {
      route += '?version=' + payload.version
      if (Number.isSafeInteger(payload.part) && payload.part >= 0) route += '&part=' + payload.part
    }
    if (payload.action === 'upload' && (typeof payload.body?.content !== 'string' || payload.body.content.length > 28000000)) throw new Error(t('desktop.file_is_too_large_or_invalid'))
    return requestGateway(route, { method, body: method === 'GET' ? undefined : payload.body }, 150000)
  })
  ipcMain.handle('office-desktop:listOfficeModels', async () => requestGateway('/api/office/models'))
  ipcMain.handle('office-desktop:getOfficeModel', async (_event, modelId) => (
    requestGateway(`/api/office/models/${encodeURIComponent(modelId)}`)
  ))
  ipcMain.handle('office-desktop:updateOfficeModel', async (_event, modelId, payload) => (
    requestGateway(`/api/office/models/${encodeURIComponent(modelId)}`, { method: 'PUT', body: payload }, MODEL_SETTINGS_REQUEST_TIMEOUT_MS)
  ))
  ipcMain.handle('office-desktop:addOfficeModel', async (_event, payload) => (
    requestGateway('/api/office/models', { method: 'POST', body: payload }, MODEL_SETTINGS_REQUEST_TIMEOUT_MS)
  ))
  ipcMain.handle('office-desktop:setOfficeDefaultModel', async (_event, modelId) => (
    requestGateway('/api/office/models/default', { method: 'PUT', body: { modelId } })
  ))
  ipcMain.handle('office-desktop:listSkills', async () => requestGateway('/api/skills'))
  ipcMain.handle('office-desktop:getOfficeBootstrap', async () => (
    requestGateway('/api/office/bootstrap')
  ))
  ipcMain.handle('office-desktop:getOfficeSettings', async () => (
    requestGateway('/api/office/settings')
  ))
  ipcMain.handle('office-desktop:getLanguage', () => localization.getLanguage())
  ipcMain.handle('office-desktop:setLanguage', (event, language) => { requireMainFrame(event); return localization.setLanguage(language) })
  ipcMain.handle('office-desktop:listExecutionApprovals', event => {
    requireMainFrame(event)
    return requestGateway('/api/office/execution-approvals')
  })
  ipcMain.handle('office-desktop:decideExecutionApproval', (event, id, hash, decision) => {
    requireMainFrame(event)
    return requestGateway(`/api/office/execution-approvals/${encodeURIComponent(id)}/decision`,
      { method: 'POST', body: { request_hash: hash, decision } })
  })
  ipcMain.handle('office-desktop:listMcpServices', event => {
    requireMainFrame(event)
    return requestGateway('/api/mcp/services')
  })
  ipcMain.handle('office-desktop:changeMcpService', (event, name, body) => {
    requireMainFrame(event)
    return requestGateway('/api/mcp/services/' + encodeURIComponent(name), { method: 'POST', body }, 35000)
  })
  ipcMain.handle('office-desktop:getOfficeEmployeeTools', (event, id) => {
    requireMainFrame(event)
    return requestGateway(`/api/office/employees/${encodeURIComponent(id)}/tools`)
  })
  ipcMain.handle('office-desktop:saveOfficeEmployeeTools', (event, id, payload) => {
    requireMainFrame(event)
    return requestGateway(`/api/office/employees/${encodeURIComponent(id)}/tools`, { method: 'PUT', body: payload })
  })
  ipcMain.handle('office-desktop:saveOfficeSettings', async (_event, payload) => (
    requestGateway('/api/office/settings', { method: 'PATCH', body: payload }, MODEL_SETTINGS_REQUEST_TIMEOUT_MS)
  ))
  ipcMain.handle('office-desktop:getUserMemory', async () => requestGateway('/api/memory'))
  ipcMain.handle('office-desktop:getEmployeeMemory', async (_event, employeeId) => (
    requestGateway(`/api/office/employees/${encodeURIComponent(employeeId)}/memory`)
  ))
  ipcMain.handle('office-desktop:listWorkLogDays', (_event, employeeId, before) =>
    requestGateway(`/api/office/employees/${encodeURIComponent(employeeId)}/work-logs${before ? `?before=${encodeURIComponent(before)}` : ''}`))
  ipcMain.handle('office-desktop:getWorkLogDay', (_event, employeeId, day) => {
    if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('Invalid work log date')
    return requestGateway(`/api/office/employees/${encodeURIComponent(employeeId)}/work-logs/${day}`)
  })
  ipcMain.handle('office-desktop:createMemoryFact', async (_event, payload) => (
    requestGateway('/api/memory/facts', { method: 'POST', body: payload })
  ))
  ipcMain.handle('office-desktop:updateMemoryFact', async (_event, factId, payload) => (
    requestGateway(`/api/memory/facts/${encodeURIComponent(factId)}`, { method: 'PATCH', body: payload })
  ))
  ipcMain.handle('office-desktop:deleteMemoryFact', async (_event, factId) => (
    requestGateway(`/api/memory/facts/${encodeURIComponent(factId)}`, { method: 'DELETE' })
  ))
  ipcMain.handle('office-desktop:testOfficeBootstrapModel', async (_event, payload) => (
    requestGateway('/api/office/bootstrap/test-model', { method: 'POST', body: payload }, MODEL_SETTINGS_REQUEST_TIMEOUT_MS)
  ))
  ipcMain.handle('office-desktop:initializeOfficeBootstrap', async (_event, payload) => (
    requestGateway('/api/office/bootstrap', { method: 'POST', body: payload }, MODEL_SETTINGS_REQUEST_TIMEOUT_MS)
  ))
  ipcMain.handle('office-desktop:createOfficeEmployee', async (_event, payload) => (
    requestGateway('/api/office/employees', { method: 'POST', body: payload })
  ))
  ipcMain.handle('office-desktop:getOfficeEmployee', async (_event, employeeId) => (
    requestGateway(`/api/office/employees/${encodeURIComponent(employeeId)}`)
  ))
  ipcMain.handle(
    'office-desktop:saveOfficeEmployeePersonalInfo',
    async (_event, employeeId, payload) => requestGateway(
      `/api/office/employees/${encodeURIComponent(employeeId)}/personal-info`,
      { method: 'PUT', body: payload },
    ),
  )
  ipcMain.handle('office-desktop:deleteOfficeEmployee', async (_event, employeeId) => (
    requestGateway(`/api/office/employees/${encodeURIComponent(employeeId)}`, { method: 'DELETE' })
  ))
  ipcMain.handle('office-desktop:sendOfficeMessage', async (_event, command) => (
    requestGateway('/api/office/messages', { method: 'POST', body: command })
  ))
  ipcMain.handle('office-desktop:listOfficeGroupMessages', async (_event, afterSequence) => {
    const query = afterSequence === undefined
      ? ''
      : `?afterSequence=${encodeURIComponent(afterSequence)}`
    return requestGateway(`/api/office/group/messages${query}`)
  })
  ipcMain.handle(
    'office-desktop:listOfficeConversationMessages',
    async (_event, employeeId, kind) => requestGateway(
      `/api/office/employees/${encodeURIComponent(employeeId)}`
        + `/conversations/${encodeURIComponent(kind)}/messages?limit=500`,
    ),
  )
  ipcMain.handle('office-desktop:listOfficeQueue', async () => requestGateway('/api/office/queue'))
  ipcMain.handle('office-desktop:editOfficeQueue', async (_event, queueId, text) => (
    requestGateway(`/api/office/queue/${encodeURIComponent(queueId)}`, {
      method: 'PATCH',
      body: { text },
    })
  ))
  ipcMain.handle('office-desktop:deleteOfficeQueue', async (_event, queueId) => (
    requestGateway(`/api/office/queue/${encodeURIComponent(queueId)}`, { method: 'DELETE' })
  ))
  ipcMain.handle('office-desktop:cancelOfficeRun', async (_event, employeeId, runId) => (
    requestGateway(
      `/api/office/employees/${encodeURIComponent(employeeId)}`
        + `/runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST' },
    )
  ))
  ipcMain.handle('office-desktop:getCurrentOfficeWorkflow', () => requestGateway('/api/office/workflows/current'))
  ipcMain.handle('office-desktop:getOfficeWorkflow', (_event, id) => requestGateway(`/api/office/workflows/${encodeURIComponent(id)}`))
  ipcMain.handle('office-desktop:getOfficeGroupExecution', () => requestGateway('/api/office/group/execution'))
  ipcMain.handle('office-desktop:pauseOfficeGroup', () => requestGateway('/api/office/group/pause', { method: 'POST' }))
  // Clearing several stores takes longer than a plain read, and cutting it off midway
  // is exactly the half-reset this endpoint exists to make unnecessary.
  ipcMain.handle('office-desktop:resetOfficeGroup', () => requestGateway('/api/office/group/reset', { method: 'POST' }, 30000))
  ipcMain.handle('office-desktop:resumeOfficeRun', async (_event, employeeId, runId, payload) => (
    requestGateway(
      `/api/office/employees/${encodeURIComponent(employeeId)}`
        + `/runs/${encodeURIComponent(runId)}/resume`,
      { method: 'POST', body: payload },
      MODEL_SETTINGS_REQUEST_TIMEOUT_MS,
    )
  ))
  ipcMain.handle('office-desktop:getOfficeRun', async (_event, employeeId, runId) => (
    requestGateway(
      `/api/office/employees/${encodeURIComponent(employeeId)}`
        + `/runs/${encodeURIComponent(runId)}`,
    )
  ))
  ipcMain.handle('office-desktop:listOfficeRuns', async (_event, employeeId, kind, limit = 20) => (
    requestGateway(
      `/api/office/employees/${encodeURIComponent(employeeId)}`
        + `/runs?conversationKind=${encodeURIComponent(kind)}`
        + `&limit=${encodeURIComponent(limit)}`,
    )
  ))
  ipcMain.handle(
    'office-desktop:listOfficeRunEvents',
    async (_event, employeeId, runId, afterSeq) => {
      const query = afterSeq === undefined ? '' : `?after_seq=${encodeURIComponent(afterSeq)}`
      return requestGateway(
        `/api/office/employees/${encodeURIComponent(employeeId)}`
          + `/runs/${encodeURIComponent(runId)}/events${query}`,
      )
    },
  )
  ipcMain.handle('office-desktop:getZoomFactor', async () => getZoomFactor())
  ipcMain.handle('office-desktop:setZoomFactor', async (_event, factor) => setZoomFactor(factor))
  ipcMain.handle('office-desktop:zoomBy', async (_event, delta) => setZoomFactor(getZoomFactor() + delta))
  ipcMain.handle('office-desktop:resetZoom', async () => setZoomFactor(DEFAULT_ZOOM_FACTOR))
}

if (ownsInstance) app.whenReady().then(async () => {
  localization = createLocalization(app.getPath('userData'), app.getLocale())
  openGatewayLog()
  logGatewayEvent('app-ready', `pid=${process.pid}`)
  if (packagedResources) {
    const { assertPortAvailable } = require('./dev-startup.cjs')
    await assertPortAvailable(Number(gatewayPort), gatewayHost)
    await new Promise((resolve, reject) => execFile(packagedPython,
      [path.join(packagedResources, 'initialize.py'), packagedResources, path.dirname(runtimeHome)],
      { cwd: backendRoot, env: gatewayEnv(gatewayHost, gatewayPort), windowsHide: true, timeout: 60000 },
      (error, stdout, stderr) => {
        if (error) return reject(new Error(t('desktop.installation_setup_failed_value', { value1: (stderr || error.message) })))
        logGatewayEvent('installation-ready', stdout.trim())
        resolve()
      }))
  }
  if (!app.isPackaged && process.argv.includes('--office-dev')) {
    const { assertPortAvailable, startDevelopmentRenderer } = require('./dev-startup.cjs')
    if (!usesExternalGateway) await assertPortAvailable(Number(gatewayPort), gatewayHost)
    developmentRenderer = await startDevelopmentRenderer(path.dirname(backendRoot))
    process.env.OFFICE_RENDERER_URL = developmentRenderer.url
    logGatewayEvent('renderer-ready', developmentRenderer.url)
  }
  registerIpcHandlers()
  createWindow()
  try {
    await pingGatewayService()
  } catch (error) {
    setServiceStatus({ lifecycle: 'error', detail: error instanceof Error ? error.message : t(gatewayStatusDetails.startupFailed) })
  }
  // 首次启动失败也要开心跳：下一轮它会自己重试，而不是等用户去点界面。
  if (!isAppQuitting) startGatewayHeartbeat()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}).catch(error => {
  logGatewayEvent('startup-failed', error.message)
  dialog.showErrorBox(t('desktop.small_office_failed_to_start'), t('desktop.value_log_value', { value1: (error.message), value2: (gatewayLogPath) }))
  process.exitCode = 1
  app.quit()
})

app.on('before-quit', (event) => {
  // 先停心跳，免得它和下面这次 kill 抢同一个进程。
  stopGatewayHeartbeat()
  logGatewayEvent('app-quit', `pid=${process.pid} gatewayPid=${gatewayProcess?.pid ?? 'none'}`)
  isAppQuitting = true
  if (gatewayProcess && gatewayProcess.exitCode === null) {
    stopOwnedProcess(gatewayProcess, { processGroup: process.platform === 'darwin' })
  }
  if (developmentRenderer && !developmentRendererClosed) {
    event.preventDefault()
    developmentRendererClosed = true
    developmentRenderer.close().catch(error => {
      writeGatewayLog(`[renderer-close-error] ${error.message}`)
    }).finally(() => app.quit())
    return
  }
  try {
    gatewayLogStream?.end()
    gatewayLogStream = null
  } catch {}
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
