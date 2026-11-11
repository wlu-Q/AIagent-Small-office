// One reproducible command owns both test processes and their isolated data.
const { spawn } = require('node:child_process')
const { gatewayEnv } = require('./gateway-env.cjs')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const root = path.resolve(__dirname, '..')
const data = fs.mkdtempSync(path.join(fs.realpathSync.native(os.tmpdir()), 'office-stage4-gateway-'))
const logPath = path.join(data, 'gateway.log')
const restartFile = path.join(data, 'restart-ask.json')
let gateway
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function startGateway(port, reuse = false) {
  const log = fs.openSync(logPath, 'a')
  gateway = spawn(path.join(root, 'backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    ['tests/office_gateway_fixture.py', path.join(data, 'home'), String(port), ...(reuse ? ['--reuse'] : [])], { cwd: path.join(root, 'backend'),
      env: { ...gatewayEnv('127.0.0.1', port), OPENBLAS_NUM_THREADS: '1', OMP_NUM_THREADS: '1',
        ...(process.argv.includes('--bootstrap') ? { OFFICE_TEST_BOOTSTRAP: '1' } : {}),
        ...(process.argv.includes('--delegation-limits') ? { OFFICE_TEST_DELEGATION_LIMITS: '1' } : {}),
        ...(process.argv.includes('--workspaces') ? { OFFICE_TEST_WORKSPACES: '1' } : {}),
        ...(process.argv.includes('--terminal') ? { OFFICE_TEST_TERMINAL: '1' } : {}),
        ...(process.argv.includes('--delivery') ? { OFFICE_TEST_DELIVERY: '1' } : {}),
        NO_PROXY: [process.env.NO_PROXY || process.env.no_proxy, '127.0.0.1', 'localhost'].filter(Boolean).join(',') },
      windowsHide: true, stdio: ['ignore', log, log] })
  fs.closeSync(log)
  const url = `http://127.0.0.1:${port}`
  let healthy = false
  for (let i = 0; i < 180 && gateway.exitCode === null; i++) {
    try { healthy = (await fetch(`${url}/health`, { signal: AbortSignal.timeout(1000) })).ok } catch {}
    if (healthy) break
    await sleep(250)
  }
  if (!healthy) throw new Error('Test Gateway did not become healthy')
}
async function stopGateway() {
  if (gateway && gateway.exitCode === null && gateway.signalCode === null) {
    const exited = new Promise(resolve => gateway.once('exit', resolve))
    gateway.kill()
    await exited
  }
}
async function runDesktop(port, scenario) {
  const desktop = spawn(require('electron'), [path.join(__dirname, 'gateway-integration-smoke.cjs')], {
    cwd: root, env: { ...process.env, DEER_FLOW_GATEWAY_URL: `http://127.0.0.1:${port}`,
      OFFICE_TEST_SCENARIO: scenario, OFFICE_TEST_RESTART_FILE: restartFile },
    windowsHide: true, stdio: 'inherit', timeout: 300000,
  })
  const code = await new Promise((resolve, reject) => { desktop.once('error', reject); desktop.once('exit', resolve) })
  if (code !== 0) throw new Error(`Desktop integration failed with exit code ${code}`)
}
async function run() {
  const server = net.createServer()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  await startGateway(port)
  if (process.argv.includes('--bootstrap')) {
    await runDesktop(port, 'bootstrap')
    console.log(`LIVE_BOOTSTRAP_OK data=${data}`)
    return
  }
  if (process.argv.includes('--delivery')) {
    await runDesktop(port, 'delivery')
    console.log(`LIVE_DELIVERY_OK data=${data}`)
    return
  }
  if (process.argv.includes('--terminal')) {
    await runDesktop(port, 'terminal')
    console.log(`LIVE_TERMINAL_OK data=${data}`)
    return
  }
  if (process.argv.includes('--workspaces')) {
    await runDesktop(port, 'workspaces')
    console.log(`LIVE_TASK_WORKSPACES_OK data=${data}`)
    return
  }
  if (process.argv.includes('--delegation-limits')) {
    await runDesktop(port, 'delegation-limits')
    console.log(`LIVE_DELEGATION_LIMITS_OK data=${data}`)
    return
  }
  if (process.argv.includes('--private-message-identity')) {
    await runDesktop(port, 'private-message-identity')
    console.log(`LIVE_PRIVATE_MESSAGE_IDENTITY_OK data=${data}`)
    return
  }
  if (process.argv.includes('--visualization')) {
    await runDesktop(port, 'visualization')
    console.log(`LIVE_VISUALIZATION_GATEWAY_OK data=${data}`)
    return
  }
  if (process.argv.includes('--file-preview')) {
    await runDesktop(port, 'file-preview')
    console.log(`LIVE_FILE_PREVIEW_GATEWAY_OK data=${data}`)
    return
  }
  if (process.argv.includes('--model-settings') || process.argv.includes('--model-edit')) {
    await runDesktop(port, process.argv.includes('--model-edit') ? 'model-edit' : 'model-settings')
    console.log(`LIVE_MODEL_GATEWAY_OK data=${data}`)
    return
  }
  await runDesktop(port, 'standard')
  await runDesktop(port, 'restart-prepare')
  await stopGateway()
  let stillServing = false
  try { stillServing = (await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(1000) })).ok } catch {}
  if (stillServing) throw new Error('Old Gateway is still serving; process restart was not achieved')
  await startGateway(port, true)
  await runDesktop(port, 'restart-verify')
  console.log(`LIVE_GATEWAY_INTEGRATION_OK data=${data}`)
}
run().catch(error => {
  console.error(error)
  console.error(`Gateway diagnostics: ${logPath}`)
  if (fs.existsSync(logPath)) console.error(fs.readFileSync(logPath, 'utf8').split(/\r?\n/).slice(-120).join('\n'))
  process.exitCode = 1
})
  .finally(stopGateway)
