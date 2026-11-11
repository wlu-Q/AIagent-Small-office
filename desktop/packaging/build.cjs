const path = require('node:path')
const fs = require('node:fs')
const { spawnSync } = require('node:child_process')
const root = path.resolve(__dirname, '../..')
const packageJson = require('../../package.json')
const runtimes = require('./runtimes.json')
if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Build requires Windows x64')
if (process.versions.node !== packageJson.engines.node || runtimes.node.version !== packageJson.engines.node) {
  throw new Error(`Build requires Node ${packageJson.engines.node}; found ${process.versions.node}`)
}
const sourcePython = path.join(root, 'backend/.venv/Scripts/python.exe')
const bundledNode = path.join(root, 'output/packaging/resources/node/node.exe')
function run(command, args, env = process.env) {
  const result = spawnSync(command, args, { cwd: root, env, stdio: 'inherit', windowsHide: true })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${path.basename(command)} failed (${result.status})`)
}
const pythonVersion = fs.readFileSync(path.join(root, 'backend/.python-version'), 'utf8').trim()
run(sourcePython, ['-c', `import platform; assert platform.python_version() == '${pythonVersion}', 'Run uv sync --locked in backend to prepare Python ${pythonVersion}'`])
if (!fs.existsSync(path.join(root, 'node_modules/electron/dist/electron.exe'))) {
  throw new Error('Electron runtime is missing; run npm ci before building')
}
run(sourcePython, ['-X', 'utf8', path.join(__dirname, 'prepare-runtimes.py')])
const env = { ...process.env }
const oldPath = env.Path || env.PATH || ''
for (const key of Object.keys(env)) if (key.toLowerCase() === 'path') delete env[key]
env.Path = `${path.dirname(bundledNode)}${path.delimiter}${oldPath}`
// Use the same pinned Node for frontend compilation and packaging.
run(process.execPath, [path.join(root, 'client/node_modules/typescript/bin/tsc'), '-p', 'client/tsconfig.json'])
run(process.execPath, [path.join(root, 'client/node_modules/vite/bin/vite.js'), 'build', 'client'])
run(sourcePython, ['-X', 'utf8', path.join(__dirname, 'prepare.py')], env)
run(path.join(root, 'output/packaging/resources/python/python.exe'),
  ['-X', 'utf8', path.join(__dirname, 'test_release.py')], env)
run(bundledNode, ['--test', path.join(__dirname, 'uninstall.test.cjs')], env)
run(bundledNode, [path.join(root, 'node_modules/electron-builder/out/cli/cli.js'),
  '--config', path.join(__dirname, 'builder.cjs'), '--win', '--x64', '--publish', 'never'], env)
run(bundledNode, [path.join(__dirname, 'verify-package.cjs')], env)
