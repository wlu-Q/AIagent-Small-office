const { spawn } = require('node:child_process')
const { backendRoot, gatewayEnv } = require('./gateway-env.cjs')

const host = '127.0.0.1'
const port = '8001'
const gateway = spawn('uv', [
  'run', '--no-editable', 'python', '-m', 'uvicorn', 'app.gateway.app:app',
  '--host', host, '--port', port, '--loop', 'asyncio:SelectorEventLoop',
], {
  cwd: backendRoot,
  env: gatewayEnv(host, port),
  stdio: 'inherit',
  windowsHide: true,
})

gateway.on('error', error => {
  console.error(`Gateway 启动失败：${error.message}`)
  process.exitCode = 1
})
gateway.on('exit', code => { process.exitCode = code ?? 1 })
