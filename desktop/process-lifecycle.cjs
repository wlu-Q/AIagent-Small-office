const { spawnSync } = require('node:child_process')

// Only use a live ChildProcess handle we created; never kill by port or process name.
function stopOwnedProcess(child, { processGroup = false } = {}) {
  if (!child?.pid || child.exitCode !== null || child.signalCode != null) return
  if (process.platform === 'win32') {
    const result = spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true, stdio: 'ignore', timeout: 10000,
    })
    if (result.status === 0) return
  }
  if (process.platform !== 'win32' && processGroup) {
    try { process.kill(-child.pid, 'SIGKILL') } catch (error) {
      if (error.code !== 'ESRCH') throw error
    }
    return
  }
  try { child.kill() } catch {}
}

module.exports = { stopOwnedProcess }
