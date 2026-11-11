const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const { spawn, spawnSync } = require('node:child_process')
const { stopOwnedProcess } = require('./process-lifecycle.cjs')
const { startDevelopmentRenderer, assertPortAvailable, checkPrerequisites } = require('./dev-startup.cjs')

test('Gateway uses the bundled PowerShell without a developer PATH', { skip: process.platform !== 'win32' }, () => {
  const root = path.resolve(__dirname, '..')
  const environment = { ...process.env }
  delete environment.OFFICE_POWERSHELL_EXECUTABLE
  delete environment.OFFICE_PACKAGED_RESOURCES
  for (const key of Object.keys(environment)) if (key.toLowerCase() === 'path') delete environment[key]
  environment.Path = `${process.env.SystemRoot}\\System32`
  const probe = `const {gatewayEnv}=require('./desktop/gateway-env.cjs');
    const result=require('node:child_process').spawnSync(${JSON.stringify(path.join(root, 'backend/.venv/Scripts/python.exe'))},
      ['-c', 'from deerflow.office.terminal import powershell_executable; print(powershell_executable()[0])'],
      {env:gatewayEnv('127.0.0.1','8001'),encoding:'utf8',windowsHide:true});
    process.stdout.write(result.stdout || ''); process.stderr.write(result.stderr || ''); process.exit(result.status ?? 1);`
  const result = spawnSync(process.execPath, ['-e', probe], { cwd: root, env: environment, encoding: 'utf8', windowsHide: true, timeout: 20000 })
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.stdout.trim().toLowerCase(), path.join(root, 'output/packaging/resources/powershell/pwsh.exe').toLowerCase())
})

test('explicit PowerShell configuration is passed through without silently replacing it', () => {
  const { gatewayEnv } = require('./gateway-env.cjs')
  const previous = process.env.OFFICE_POWERSHELL_EXECUTABLE
  try {
    process.env.OFFICE_POWERSHELL_EXECUTABLE = 'C:\\missing\\pwsh.exe'
    assert.equal(gatewayEnv('127.0.0.1', '8001').OFFICE_POWERSHELL_EXECUTABLE, 'C:\\missing\\pwsh.exe')
    assert.throws(() => checkPrerequisites(path.resolve(__dirname, '..')), /runtime:powershell/)
  } finally {
    if (previous === undefined) delete process.env.OFFICE_POWERSHELL_EXECUTABLE
    else process.env.OFFICE_POWERSHELL_EXECUTABLE = previous
  }
})

test('Windows double-click entry uses CRLF so cmd parses the Chinese script correctly', () => {
  const script = fs.readFileSync(path.join(__dirname, '../start-dev.cmd'), 'utf8')
  assert.equal(/(?<!\r)\n/.test(script), false)
})

test('missing dependencies produce an actionable error without installing anything', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'office-start-中文 '))
  try {
    assert.throws(() => checkPrerequisites(root), /npm ci/)
    assert.deepEqual(fs.readdirSync(root), [])
  } finally { fs.rmdirSync(root) }
})

test('an incomplete Electron install is reported without executing its downloader', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'office-preflight-'))
  t.after(() => {
    assert.ok(path.resolve(root).startsWith(path.resolve(os.tmpdir()) + path.sep + 'office-preflight-'))
    fs.rmSync(root, { recursive: true, force: true })
  })
  const electronDirectory = path.join(root, 'node_modules/electron')
  fs.mkdirSync(electronDirectory, { recursive: true })
  fs.writeFileSync(path.join(electronDirectory, 'package.json'), JSON.stringify({ name: 'electron', main: 'index.js' }))
  fs.writeFileSync(path.join(electronDirectory, 'index.js'), "require('node:fs').writeFileSync(__dirname + '/downloader-ran', 'unexpected')")
  assert.throws(() => checkPrerequisites(root), /Electron 安装不完整.*npm ci/)
  assert.equal(fs.existsSync(path.join(electronDirectory, 'downloader-ran')), false)
})

test('an occupied Gateway port is rejected without touching its owner', async () => {
  const other = net.createServer(socket => socket.end())
  await new Promise(resolve => other.listen(0, '127.0.0.1', resolve))
  try {
    await assert.rejects(assertPortAvailable(other.address().port), /占用/)
    assert.equal(other.listening, true)
  } finally { await new Promise(resolve => other.close(resolve)) }
})

test('real Vite serves this checkout on its own port and releases it on close', async () => {
  const root = path.resolve(__dirname, '..')
  const renderer = await startDevelopmentRenderer(root)
  const port = Number(new URL(renderer.url).port)
  try {
    const html = await (await fetch(renderer.url)).text()
    assert.match(html, /Small Office/)
    assert.match(html, /\/src\/index.tsx/)
    assert.equal(new URL(renderer.url).hostname, '127.0.0.1')
  } finally { await renderer.close() }
  await assertPortAvailable(port)
})

test('stopping an owned Windows process also stops its child without touching other services', {
  skip: process.platform !== 'win32', timeout: 20000,
}, async () => {
  const unrelated = net.createServer()
  await new Promise(resolve => unrelated.listen(0, '127.0.0.1', resolve))
  const childCode = "const s=require('node:net').createServer(); s.listen(0,'127.0.0.1',()=>process.send({port:s.address().port}));"
  const parentCode = `const c=require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(childCode)}],{stdio:['ignore','ignore','ignore','ipc'],windowsHide:true}); c.on('message',m=>process.send(m)); setInterval(()=>{},1000);`
  const parent = spawn(process.execPath, ['-e', parentCode], { stdio: ['ignore', 'ignore', 'ignore', 'ipc'], windowsHide: true })
  try {
    const { port } = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('child did not become ready')), 5000)
      parent.once('message', value => { clearTimeout(timeout); resolve(value) })
      parent.once('error', error => { clearTimeout(timeout); reject(error) })
    })
    stopOwnedProcess(parent)
    await assertPortAvailable(port)
    assert.equal(unrelated.listening, true)
  } finally {
    stopOwnedProcess(parent)
    await new Promise(resolve => unrelated.close(resolve))
  }
})
