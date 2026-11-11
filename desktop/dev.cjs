const fs = require('node:fs')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { checkPrerequisites } = require('./dev-startup.cjs')
const { stopOwnedProcess } = require('./process-lifecycle.cjs')

// 与 desktop/main.cjs 的约定：拿不到单实例锁时用它作为退出码。
// 退出码比 stdout 可靠——控制台一关，输出就没了，退出码还是能拿到。
const ALREADY_RUNNING_EXIT_CODE = 42

const root = path.resolve(__dirname, '..')
const logPath = path.join(root, 'output/logs/development.log')
fs.mkdirSync(path.dirname(logPath), { recursive: true })
const log = fs.createWriteStream(logPath, { flags: 'a' })
log.on('error', error => console.error(`无法写入启动日志：${error.message}`))
function report(message) { console.log(message); log.write(`${new Date().toISOString()} ${message}\n`) }

// 双击 start-dev.cmd 走 --detach：桌面端脱离启动器的控制台，启动器 2 秒后自己退出，
// 黑窗口随之下线，应用继续运行。原来的问题是桌面端挂在启动器控制台下面，关掉黑窗口
// 会让整个进程组收到关闭事件，应用和 Gateway 一起死——「窗口闪一下就没」就是这样来的。
// 而 `npm run dev` 保持前台监管，Ctrl+C 仍然可以停掉整个应用。
const detach = process.argv.includes('--detach')

async function startDetached(electron, env) {
  const outFd = fs.openSync(logPath, 'a')
  let child
  try {
    child = spawn(electron, [root, '--office-dev'], {
      cwd: root,
      env,
      windowsHide: true,
      detached: true,
      stdio: ['ignore', outFd, outFd],
    })
  } finally {
    fs.closeSync(outFd)
  }
  child.unref()

  // 只守最前面 2 秒：够接住「已有一个在跑」和启动阶段退出，又不会一直占着黑窗口。
  let earlyExit = null
  let earlyError = null
  child.on('exit', (code) => { earlyExit = code ?? 1 })
  child.on('error', (error) => { earlyError = error })
  await new Promise(resolve => setTimeout(resolve, 2000))

  if (earlyError) {
    report(`启动失败：${earlyError.message}`)
    process.exitCode = 1
  } else if (earlyExit === ALREADY_RUNNING_EXIT_CODE) {
    report('小小办公室已经在运行，已切换到现有窗口。这个窗口可以关闭。')
    process.exitCode = 2
  } else if (earlyExit !== null) {
    report(`桌面端在启动阶段退出（${earlyExit}）。详情见 ${logPath}`)
    process.exitCode = 1
  } else {
    report('桌面窗口正在启动（约 10 秒），这个窗口可以关闭，不会影响应用。')
  }
}

function startSupervised(electron, env) {
  const child = spawn(electron, [root, '--office-dev'], { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { process.stdout.write(chunk); log.write(chunk) })
  child.on('error', error => { report(`启动失败：${error.message}`); process.exitCode = 1; log.end() })
  child.on('exit', code => {
    process.exitCode = code ?? 1
    if (code === ALREADY_RUNNING_EXIT_CODE) report('小小办公室已经在运行，已切换到现有窗口。')
    else report(`桌面端已退出（${code}）。`)
    log.end()
  })
  // Closing the console must not leave Electron/Python descendants behind.
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => stopOwnedProcess(child))
  process.on('exit', () => stopOwnedProcess(child))
}

async function main() {
  report('正在检查小小办公室开发环境…')
  const electron = checkPrerequisites(root)
  if (process.argv.includes('--check')) {
    report('开发环境检查通过。')
    log.end()
    return
  }
  const env = { ...process.env }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.OFFICE_RENDERER_URL
  report(`正在启动桌面窗口。启动日志：${logPath}`)
  if (detach) {
    await startDetached(electron, env)
    log.end()
    return
  }
  startSupervised(electron, env)
}

main().catch(error => {
  report(`启动失败：${error.message}`)
  process.exitCode = 1
  log.end()
})
