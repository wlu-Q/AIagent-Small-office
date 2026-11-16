// Runs the actual packaged app with isolated user data and a local model fixture.
const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const { spawn } = require('node:child_process')
const os = require('node:os')
const { stopOwnedProcess } = require('../process-lifecycle.cjs')
const { packageLayout } = require('./layout.cjs')
const root = path.resolve(__dirname, '../..')
const requestedExecutable = process.argv.slice(2).find(argument => !argument.startsWith('--'))
const executable = requestedExecutable ? path.resolve(requestedExecutable) : packageLayout(root).executable
const defaultProfile = process.argv.includes('--default-profile')
const freshProfile = process.argv.includes('--fresh-profile')
const sessionDirectory = process.argv.find(argument => argument.startsWith('--session-dir='))?.slice('--session-dir='.length)
const folder = sessionDirectory ? path.resolve(sessionDirectory)
  : path.join(root, 'output/packaging', freshProfile ? `smoke-${Date.now()}` : 'smoke')
fs.mkdirSync(folder, { recursive: true })
let child
const server = http.createServer(async (request, response) => {
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  const input = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
  let message = { role: 'assistant', content: '连接成功' }
  const planned = path.join(folder, 'model-responses.json')
  if (input.tools?.length && fs.existsSync(planned)) {
    const scenario = JSON.parse(fs.readFileSync(planned, 'utf8'))
    const latest = input.messages?.findLast(item => item.role === 'user')
    if (JSON.stringify(latest?.content).includes(scenario.marker)) {
      const observed = new Set(input.messages.filter(item => item.role === 'tool').map(item => item.tool_call_id))
      message = scenario.steps.find(step => !step.tool_calls?.every(call => observed.has(call.id))) || scenario.steps.at(-1)
    }
  }
  const finishReason = message.tool_calls?.length ? 'tool_calls' : 'stop'
  if (input.stream) {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    for (const choice of [
      { index: 0, delta: { ...message,
        ...(message.tool_calls ? { tool_calls: message.tool_calls.map((call, index) => ({ ...call, index })) } : {}) }, finish_reason: null },
      { index: 0, delta: {}, finish_reason: finishReason },
    ]) response.write(`data: ${JSON.stringify({ id: 'package-check', object: 'chat.completion.chunk',
      created: 1, model: 'package-check', choices: [choice] })}\n\n`)
    response.end('data: [DONE]\n\n')
    return
  }
  response.writeHead(200, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify({ id: 'package-check', object: 'chat.completion', created: 1,
    model: 'package-check', choices: [{ index: 0, message, finish_reason: finishReason }],
    usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }))
})
async function main() {
  // Reuse the fixture port across restarts, so the persisted test model stays valid.
  const previous = path.join(folder, 'session.json')
  const old = fs.existsSync(previous) ? JSON.parse(fs.readFileSync(previous, 'utf8')) : {}
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(old.modelPort || 0, '127.0.0.1', resolve)
  })
  const probe = net.createServer()
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
  const cdpPort = probe.address().port
  await new Promise(resolve => probe.close(resolve))
  const modelPort = server.address().port
  const env = { ...process.env }
  env.NO_PROXY = [env.NO_PROXY || env.no_proxy, '127.0.0.1', 'localhost'].filter(Boolean).join(',')
  delete env.ELECTRON_RUN_AS_NODE
  for (const name of Object.keys(env)) if (name.toLowerCase() === 'path') delete env[name]
  // Prove runtime discovery does not depend on the developer's Python/Node/Git/Pwsh.
  if (process.platform === 'win32') env.Path = `${process.env.SystemRoot}\\System32;${process.env.SystemRoot}`
  else env.PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
  const log = fs.openSync(path.join(folder, 'desktop.log'), 'a')
  const appData = process.platform === 'win32' ? process.env.APPDATA : path.join(os.homedir(), 'Library/Application Support')
  const userData = defaultProfile ? path.join(appData, 'SmallOffice') : path.join(folder, 'user')
  child = spawn(executable,
    [...(defaultProfile ? [] : [`--user-data-dir=${userData}`]), `--remote-debugging-port=${cdpPort}`],
    { cwd: folder, env, windowsHide: false, stdio: ['ignore', log, log] })
  fs.closeSync(log)
  fs.writeFileSync(previous, JSON.stringify({ modelPort, modelUrl: `http://127.0.0.1:${modelPort}/v1`,
    cdpPort, cdpUrl: `http://127.0.0.1:${cdpPort}`, pid: child.pid, userData }, null, 2))
  child.on('exit', code => { server.close(); process.exitCode = code || 0 })
  child.on('error', error => { console.error(error.message); server.close(); process.exitCode = 1 })
  console.log(`Packaged desktop smoke session ready: ${previous}`)
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { stopOwnedProcess(child); server.close() })
process.on('exit', () => stopOwnedProcess(child))
main().catch(error => { console.error(error.message); server.close(); process.exitCode = 1 })
