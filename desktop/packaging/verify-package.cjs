const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const asar = require('@electron/asar')
const { spawnSync } = require('node:child_process')
const { runtimePaths, runtimeEnvironment } = require('../runtime-paths.cjs')
const { packageLayout } = require('./layout.cjs')

const root = path.resolve(__dirname, '../..')
const staged = path.join(root, 'output/packaging/app')
const resources = path.join(root, 'output/packaging/resources')
function verifyDirectory(directory, readPackaged) {
  let checked = 0
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === '.gitkeep' || entry.name.endsWith('.pyc')) continue
    const source = path.join(directory, entry.name)
    if (entry.isDirectory()) checked += verifyDirectory(source, readPackaged)
    else {
      assert.ok(fs.readFileSync(source).equals(readPackaged(source)), `Packaged file differs: ${source}`)
      checked += 1
    }
  }
  return checked
}

function verifyFiles(stagedApp, stagedResources, packaged, platform = process.platform) {
  let checked = verifyDirectory(stagedApp, source => asar.extractFile(path.join(packaged, 'app.asar'), path.relative(stagedApp, source)))
  for (const directory of ['backend', 'skills', 'defaults']) {
    checked += verifyDirectory(path.join(stagedResources, directory), source => fs.readFileSync(path.join(packaged, 'office', path.relative(stagedResources, source))))
  }
  for (const relative of ['initialize.py', 'release-manifest.json', 'LICENSE', 'DEERFLOW_LICENSE']) {
    assert.ok(fs.readFileSync(path.join(stagedResources, relative)).equals(fs.readFileSync(path.join(packaged, 'office', relative))), relative)
  }
  const runtimes = runtimePaths(path.join(packaged, 'office'), platform)
  const originals = runtimePaths(stagedResources, platform)
  for (const name of ['python', 'node', 'git', 'powershell']) {
    assert.ok(fs.statSync(runtimes[name]).isFile(), `Missing packaged ${name}`)
    if (platform === 'win32') assert.ok(fs.readFileSync(originals[name]).equals(fs.readFileSync(runtimes[name])), name)
    else fs.accessSync(runtimes[name], fs.constants.X_OK)
  }
  return checked
}

function verifyMac(layout) {
  const office = path.join(layout.resources, 'office')
  const runtimes = runtimePaths(office)
  const env = runtimeEnvironment(office, {
    ...process.env, PATH: '/usr/bin:/bin:/usr/sbin:/sbin',
    PYTHONHOME: path.join(office, 'python'), PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1',
    PYTHONPATH: [path.join(office, 'backend'), path.join(office, 'backend/packages/harness')].join(path.delimiter),
  })
  function execute(command, args) {
    const result = spawnSync(command, args, { cwd: layout.appOutDir, env, encoding: 'utf8', timeout: 120000 })
    assert.ifError(result.error)
    assert.equal(result.status, 0, `${path.basename(command)}: ${result.stderr}`)
    return result.stdout.trim()
  }
  const specs = require('./runtimes.macos.json')
  execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', layout.app])
  execute(runtimes.python, ['-c', 'import platform, uvicorn, fastapi, lancedb, langgraph, yaml, deerflow; assert platform.machine() == "arm64"'])
  assert.equal(execute(runtimes.node, ['-p', 'process.platform + "/" + process.arch + "/" + process.versions.node']), `darwin/arm64/${specs.node.version}`)
  assert.equal(execute(runtimes.git, ['--version']), `git version ${specs.git.version}`)
  assert.equal(execute(runtimes.powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', '$PSVersionTable.PSVersion.ToString()']), specs.powershell.version)
  if (process.env.OFFICE_MAC_RELEASE === '1') {
    execute('/usr/sbin/spctl', ['--assess', '--type', 'execute', '--verbose', layout.app])
    execute('/usr/bin/xcrun', ['stapler', 'validate', layout.app])
  }
}

if (require.main === module) {
  const layout = packageLayout(root)
  const checked = verifyFiles(staged, resources, layout.resources)
  if (process.platform === 'darwin') verifyMac(layout)
  console.log(`Packaged application matches staged code and assets: ${checked} files, bundled entry points verified.`)
}

module.exports = { verifyFiles }
