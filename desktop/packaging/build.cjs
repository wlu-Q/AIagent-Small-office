const path = require('node:path')
const fs = require('node:fs')
const { spawnSync } = require('node:child_process')
const { runtimePaths, runtimeEnvironment, developmentPython } = require('../runtime-paths.cjs')
const { packageLayout } = require('./layout.cjs')
const root = path.resolve(__dirname, '../..')
const packageJson = require('../../package.json')
const requestedPlatform = process.argv[2]
if (!['win32', 'darwin'].includes(requestedPlatform) || requestedPlatform !== process.platform) {
  throw new Error('Use package:win on Windows x64 or package:mac on native Apple Silicon macOS')
}
const layout = packageLayout(root)
const runtimes = require(`./${layout.manifest}`)
const release = process.argv.includes('--release')
if (release && process.platform !== 'darwin') throw new Error('--release is for signed macOS distribution')
if (release) {
  const env = process.env
  const credentials = (env.APPLE_ID && env.APPLE_APP_SPECIFIC_PASSWORD && env.APPLE_TEAM_ID)
    || (env.APPLE_API_KEY && env.APPLE_API_KEY_ID && env.APPLE_API_ISSUER) || env.APPLE_KEYCHAIN_PROFILE
  if (!credentials) throw new Error('Mac release requires Apple notarization credentials or APPLE_KEYCHAIN_PROFILE')
  if (env.CSC_IDENTITY_AUTO_DISCOVERY === 'false' || env.CSC_NAME === '-') {
    throw new Error('Mac release requires Developer ID signing, not disabled or ad-hoc signing')
  }
}
if (process.versions.node !== packageJson.engines.node || runtimes.node.version !== packageJson.engines.node) {
  throw new Error(`Build requires Node ${packageJson.engines.node}; found ${process.versions.node}`)
}
const sourcePython = developmentPython(path.join(root, 'backend'))
const resources = path.join(root, 'output/packaging/resources')
const bundled = runtimePaths(resources)
const bundledNode = bundled.node
function run(command, args, env = process.env) {
  const result = spawnSync(command, args, { cwd: root, env, stdio: 'inherit', windowsHide: true })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${path.basename(command)} failed (${result.status})`)
}
const pythonVersion = fs.readFileSync(path.join(root, 'backend/.python-version'), 'utf8').trim()
run(sourcePython, ['-c', `import platform; assert platform.python_version() == '${pythonVersion}', 'Run uv sync --locked in backend to prepare Python ${pythonVersion}'`])
if (!fs.existsSync(layout.electron)) {
  throw new Error('Electron runtime is missing; run npm ci before building')
}
run(sourcePython, ['-X', 'utf8', path.join(__dirname, 'prepare-runtimes.py')])
const env = runtimeEnvironment(resources)
env.OFFICE_MAC_RELEASE = release ? '1' : '0'
// Use the same pinned Node for frontend compilation and packaging.
run(bundledNode, [path.join(root, 'client/node_modules/typescript/bin/tsc'), '-p', 'client/tsconfig.json'], env)
run(bundledNode, [path.join(root, 'client/node_modules/vite/bin/vite.js'), 'build', 'client'], env)
run(sourcePython, ['-X', 'utf8', path.join(__dirname, 'prepare.py')], env)
run(bundled.python,
  ['-X', 'utf8', path.join(__dirname, 'test_release.py')], env)
if (process.platform === 'win32') run(bundledNode, ['--test', path.join(__dirname, 'uninstall.test.cjs')], env)
run(bundledNode, [path.join(root, 'node_modules/electron-builder/out/cli/cli.js'),
  '--config', path.join(__dirname, 'builder.cjs'), ...layout.builderArgs, '--publish', 'never'], env)
run(bundledNode, [path.join(__dirname, 'verify-package.cjs')], env)
if (process.platform === 'darwin' && !release) console.log('Local test DMG only: ad-hoc signed, not notarized for distribution.')
