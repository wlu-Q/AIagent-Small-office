const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const vm = require('node:vm')
const asar = require('@electron/asar')
const { packageLayout } = require('./layout.cjs')
const { runtimePaths } = require('../runtime-paths.cjs')
const { verifyFiles } = require('./verify-package.cjs')
const root = path.resolve(__dirname, '../..')

function loadBuilder(release = false, remove = () => {}) {
  const sandbox = {
    module: { exports: {} }, __dirname,
    process: { env: { OFFICE_MAC_RELEASE: release ? '1' : '0' } },
    require(name) {
      if (name === 'node:fs') return { rmSync: remove }
      return require(name)
    },
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'builder.cjs'), 'utf8'), sandbox)
  return sandbox.module.exports
}

test('packaging paths match Windows and native Apple Silicon bundle layouts', () => {
  const windows = packageLayout(root, 'win32', 'x64')
  assert.equal(windows.executable, path.join(root, 'dist/win-unpacked/SmallOffice.exe'))
  assert.equal(windows.resources, path.join(root, 'dist/win-unpacked/resources'))
  const mac = packageLayout(root, 'darwin', 'arm64')
  assert.equal(mac.executable, path.join(root, 'dist/mac-arm64/SmallOffice.app/Contents/MacOS/SmallOffice'))
  assert.equal(mac.resources, path.join(root, 'dist/mac-arm64/SmallOffice.app/Contents/Resources'))
  assert.equal(mac.electron, path.join(root, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'))
  for (const args of [['darwin', 'x64'], ['win32', 'arm64'], ['linux', 'arm64']]) {
    assert.throws(() => packageLayout(root, ...args), /native Windows x64 or macOS arm64/)
  }
})

test('builder configuration validates against the installed electron-builder schema', async () => {
  const { validateConfiguration } = require('app-builder-lib/out/util/config/config')
  for (const release of [false, true]) await validateConfiguration(loadBuilder(release), { isEnabled: false })
})

test('Mac local and release builds are explicit; Windows and upload policy stay unchanged', () => {
  const local = loadBuilder()
  const release = loadBuilder(true)
  assert.equal(local.mac.identity, '-')
  assert.equal(local.mac.notarize, false)
  assert.match(local.mac.artifactName, /-local/)
  assert.equal(release.mac.identity, undefined)
  assert.equal(release.mac.notarize, true)
  assert.equal(release.forceCodeSigning, true)
  assert.equal(release.mac.hardenedRuntime, true)
  assert.equal(release.mac.target[0].arch[0], 'arm64')
  assert.equal(release.dmg.contents[1].path, '/Applications')
  assert.equal(release.win.target[0].target, 'nsis')
  assert.equal(release.win.signExecutable, false)
  assert.equal(local.publish, null)
  assert.equal(release.publish, null)
  assert.ok(fs.existsSync(local.mac.icon))
  assert.equal(local.files.some(pattern => pattern.startsWith('docs/')), false)
})

test('afterPack only removes the owned output log, on either platform', async () => {
  const removed = []
  const builder = loadBuilder(false, file => removed.push(file))
  const { Arch } = require('builder-util')
  for (const [platform, arch] of [['win32', 'x64'], ['darwin', 'arm64']]) {
    const layout = packageLayout(root, platform, arch)
    await builder.afterPack({ appOutDir: layout.appOutDir, electronPlatformName: platform, arch: Arch[arch] })
    assert.equal(removed.at(-1), path.join(layout.appOutDir, 'debug.log'))
    await assert.rejects(() => builder.afterPack({ appOutDir: root, electronPlatformName: platform, arch: Arch[arch] }), /Unexpected release output/)
  }
  assert.equal(removed.length, 2)
})

function simulateBuild(platform, arch, flags = [], environment = {}) {
  const calls = []
  const sandbox = {
    __dirname, console: { log() {} },
    process: { platform, arch, argv: ['node', 'build.cjs', platform, ...flags], env: environment,
      versions: { node: require('../../package.json').engines.node } },
    require(name) {
      if (name === 'node:child_process') return { spawnSync: (...args) => { calls.push(args); return { status: 0 } } }
      if (name === 'node:fs') return { ...fs, existsSync: () => true }
      if (name === './layout.cjs') return { packageLayout: directory => packageLayout(directory, platform, arch) }
      if (name === '../runtime-paths.cjs') {
        const real = require(name)
        return { ...real, runtimePaths: directory => real.runtimePaths(directory, platform),
          developmentPython: directory => real.developmentPython(directory, platform),
          runtimeEnvironment: directory => real.runtimeEnvironment(directory, environment, platform) }
      }
      return require(name)
    },
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'build.cjs'), 'utf8'), sandbox)
  return calls
}

test('build orchestration uses native runtimes, stages the same sources and never publishes', () => {
  for (const [platform, arch] of [['win32', 'x64'], ['darwin', 'arm64']]) {
    const calls = simulateBuild(platform, arch)
    const build = calls.find(([, args]) => args.some(arg => arg.replaceAll('\\', '/').endsWith('electron-builder/out/cli/cli.js')))
    assert.ok(build)
    assert.ok(build[1].includes(platform === 'win32' ? '--win' : '--mac'))
    assert.ok(build[1].includes(`--${arch}`))
    assert.equal(build[1].at(-1), 'never')
    assert.equal(build[2].env.OFFICE_MAC_RELEASE, '0')
    const releaseTest = calls.find(([, args]) => args.some(arg => arg.endsWith('test_release.py')))
    assert.ok(releaseTest[0].endsWith(platform === 'win32' ? 'python.exe' : 'bin/python3'))
    const uninstall = calls.find(([, args]) => args.some(arg => arg.endsWith('uninstall.test.cjs')))
    assert.equal(Boolean(uninstall), platform === 'win32')
    const compile = calls.find(([, args]) => args.some(arg => arg.replaceAll('\\', '/').endsWith('typescript/bin/tsc')))
    assert.equal(compile[0], build[0])
  }
})

test('Mac release cannot silently downgrade to a local unsigned/unnotarized build', () => {
  assert.throws(() => simulateBuild('darwin', 'arm64', ['--release']), /notarization credentials/)
  assert.throws(() => simulateBuild('darwin', 'arm64', ['--release'], {
    APPLE_KEYCHAIN_PROFILE: 'test-profile', CSC_IDENTITY_AUTO_DISCOVERY: 'false',
  }), /Developer ID signing/)
  const calls = simulateBuild('darwin', 'arm64', ['--release'], { APPLE_KEYCHAIN_PROFILE: 'test-profile' })
  const build = calls.find(([, args]) => args.some(arg => arg.replaceAll('\\', '/').endsWith('electron-builder/out/cli/cli.js')))
  assert.equal(build[2].env.OFFICE_MAC_RELEASE, '1')
})

for (const platform of ['win32', 'darwin']) test(`package verifier detects code drift and missing runtime on ${platform}`, async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'office-package-'))
  try {
    const app = path.join(temporary, 'app')
    const resources = path.join(temporary, 'resources')
    const packaged = path.join(temporary, 'packaged')
    function write(file, text) {
      fs.mkdirSync(path.dirname(file), { recursive: true })
      fs.writeFileSync(file, text, { mode: 0o755 })
    }
    write(path.join(app, 'package.json'), '{}')
    fs.mkdirSync(packaged)
    await asar.createPackage(app, path.join(packaged, 'app.asar'))
    for (const relative of ['backend/app.py', 'skills/example/SKILL.md', 'defaults/config.yaml',
      'initialize.py', 'release-manifest.json', 'LICENSE', 'DEERFLOW_LICENSE']) {
      write(path.join(resources, relative), 'source')
      write(path.join(packaged, 'office', relative), 'source')
    }
    const original = runtimePaths(resources, platform)
    const installed = runtimePaths(path.join(packaged, 'office'), platform)
    for (const name of ['python', 'node', 'git', 'powershell']) {
      write(original[name], 'binary')
      write(installed[name], platform === 'darwin' ? 'binary with new code signature' : 'binary')
    }
    assert.equal(verifyFiles(app, resources, packaged, platform), 4)
    write(path.join(packaged, 'office/backend/app.py'), 'changed')
    assert.throws(() => verifyFiles(app, resources, packaged, platform), /Packaged file differs/)
    write(path.join(packaged, 'office/backend/app.py'), 'source')
    fs.unlinkSync(installed.powershell)
    assert.throws(() => verifyFiles(app, resources, packaged, platform), /ENOENT/)
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true })
  }
})

test('bundle executable naming matches the installed builder implementation', () => {
  const { AppInfo } = require('app-builder-lib/out/appInfo')
  const builder = loadBuilder()
  const info = new AppInfo({ config: builder, metadata: { name: 'small-office', version: '1.0.6' },
    devMetadata: {}, projectDir: root }, undefined, builder.mac)
  assert.equal(info.productFilename, 'SmallOffice')
})
