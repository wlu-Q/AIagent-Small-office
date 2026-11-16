const fs = require('node:fs')
const path = require('node:path')
const net = require('node:net')
const { createRequire } = require('node:module')
const { pathToFileURL } = require('node:url')
const { spawnSync } = require('node:child_process')
const { gatewayEnv } = require('./gateway-env.cjs')
const { developmentPython, runtimePaths } = require('./runtime-paths.cjs')

function checkPrerequisites(root) {
  const localRequire = createRequire(path.join(root, 'package.json'))
  let electronDirectory
  try { electronDirectory = path.dirname(localRequire.resolve('electron/package.json')) } catch {
    throw new Error('桌面依赖缺失，请在仓库根目录运行 npm ci')
  }
  // Resolving Electron's entry now downloads missing binaries. Preflight is read-only.
  const executableName = path.join(electronDirectory, 'path.txt')
  const electron = fs.existsSync(executableName)
    ? path.join(electronDirectory, 'dist', fs.readFileSync(executableName, 'utf8').trim()) : ''
  if (!electron || !fs.existsSync(electron)) {
    throw new Error('Electron 安装不完整，请在仓库根目录重新运行 npm ci')
  }
  try { createRequire(path.join(root, 'client/package.json')).resolve('vite') } catch {
    throw new Error('前端依赖缺失，请运行 npm --prefix client ci')
  }
  const python = developmentPython(path.join(root, 'backend'))
  if (!fs.existsSync(python)) throw new Error('后端环境缺失，请在 backend 目录运行 uv sync')
  const config = process.env.DEER_FLOW_CONFIG_PATH || path.join(root, 'config.yaml')
  if (!fs.existsSync(config)) throw new Error('缺少 config.yaml，请参考 config.example.yaml 配置模型与后端')
  // Validate imports and YAML without printing configuration values or model keys.
  const check = spawnSync(python, ['-c', 'import sys, uvicorn, deerflow, lancedb, yaml; assert sys.version_info >= (3, 12); assert isinstance(yaml.safe_load(open(sys.argv[1], encoding="utf-8")), dict)', config], {
    cwd: path.join(root, 'backend'), env: gatewayEnv('127.0.0.1', '8001'),
    windowsHide: true, timeout: 20000, stdio: 'ignore',
  })
  if (check.status !== 0) throw new Error('后端预检失败：请确认 Python 3.12+、backend 中 uv sync 已完成，以及 config.yaml 格式正确')
  if (process.platform === 'darwin') {
    if (process.arch !== 'arm64') throw new Error('当前 Mac 适配仅面向 Apple Silicon，请使用 arm64 Node 和 Electron')
    const runtimes = runtimePaths(path.join(root, 'output/packaging/resources'))
    for (const name of ['node', 'git']) {
      const result = spawnSync(runtimes[name], ['--version'], { encoding: 'utf8', timeout: 10000 })
      if (result.status !== 0) throw new Error(`Mac ${name} 运行时不可用，请运行 npm run runtime:prepare`)
    }
  }
  const terminal = spawnSync(python, ['-c', 'from deerflow.office.terminal import powershell_executable; powershell_executable()'], {
    cwd: path.join(root, 'backend'), env: gatewayEnv('127.0.0.1', '8001'),
    windowsHide: true, timeout: 20000, encoding: 'utf8',
  })
  if (terminal.status !== 0) throw new Error('办公室自带 PowerShell 不可用：请运行 npm run runtime:powershell 准备运行环境；如已指定 OFFICE_POWERSHELL_EXECUTABLE，请检查路径和版本（7.4+）')
  return electron
}

function assertPortAvailable(port, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.once('error', error => reject(new Error(`Gateway 地址 ${host}:${port} 不可用或已被占用（${error.code}）。请关闭占用服务，或显式设置 DEER_FLOW_GATEWAY_URL 使用已有 Gateway。`)))
    probe.listen({ host, port, exclusive: true }, () => probe.close(resolve))
  })
}

async function startDevelopmentRenderer(root) {
  const vite = createRequire(path.join(root, 'client/package.json')).resolve('vite')
  const { createServer } = await import(pathToFileURL(vite).href)
  const server = await createServer({
    root: path.join(root, 'client'),
    configFile: path.join(root, 'client/vite.config.mts'),
    // A private OS-assigned port cannot silently attach to another checkout's Vite.
    server: { host: '127.0.0.1', port: 0, strictPort: true, open: false },
    clearScreen: false,
  })
  try {
    await server.listen()
    const url = `http://127.0.0.1:${server.httpServer.address().port}`
    return { url, close: () => server.close() }
  } catch (error) { await server.close(); throw error }
}

module.exports = { checkPrerequisites, assertPortAvailable, startDevelopmentRenderer }
