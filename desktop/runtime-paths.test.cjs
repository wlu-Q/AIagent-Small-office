const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const vm = require('node:vm')
const { runtimePaths, runtimeEnvironment, developmentPython } = require('./runtime-paths.cjs')

test('Windows runtime paths and PATH retain the shipped layout', () => {
  const root = 'C:\\Office 安装\\resources'
  const result = runtimePaths(root, 'win32')
  assert.equal(result.python, `${root}\\python\\python.exe`)
  assert.equal(result.node, `${root}\\node\\node.exe`)
  assert.equal(result.git, `${root}\\git\\cmd\\git.exe`)
  assert.equal(result.powershell, `${root}\\powershell\\pwsh.exe`)
  const input = { PATH: 'C:\\Windows', KEEP: 'value' }
  const env = runtimeEnvironment(root, input, 'win32')
  assert.equal(env.Path, [...result.bins, input.PATH].join(';'))
  assert.equal(env.PATH, undefined)
  assert.deepEqual(input, { PATH: 'C:\\Windows', KEEP: 'value' })
  assert.equal(env.KEEP, 'value')
  assert.equal(developmentPython('C:\\Office\\backend', 'win32'), 'C:\\Office\\backend\\.venv\\Scripts\\python.exe')
})

test('Mac paths use native executables and uppercase PATH without Windows components', () => {
  const root = '/Applications/小小办公室.app/Contents/Resources/office'
  const result = runtimePaths(root, 'darwin')
  assert.equal(result.python, `${root}/python/bin/python3`)
  assert.equal(result.node, `${root}/node/bin/node`)
  assert.equal(result.git, `${root}/git/bin/git`)
  assert.equal(result.powershell, `${root}/powershell/pwsh`)
  const env = runtimeEnvironment(root, { PATH: '/usr/bin:/bin', HOME: '/Users/owner' }, 'darwin')
  assert.equal(env.PATH, [...result.bins, '/usr/bin:/bin'].join(':'))
  assert.equal(env.Path, undefined)
  assert.equal(env.HOME, '/Users/owner')
  assert.equal(developmentPython('/Users/owner/办公室/backend', 'darwin'), '/Users/owner/办公室/backend/.venv/bin/python')
  assert.throws(() => runtimePaths('/tmp/runtime', 'linux'), /Unsupported desktop platform/)
})

function gatewayModule(platform, environment) {
  const sandbox = {
    module: { exports: {} }, process: { platform, env: environment },
    __dirname: platform === 'darwin' ? '/Users/owner/办公室/desktop' : __dirname,
    require(name) {
      if (name === 'node:path') return platform === 'darwin' ? path.posix : path.win32
      return require(name)
    },
  }
  new vm.Script(fs.readFileSync(path.join(__dirname, 'gateway-env.cjs'), 'utf8')).runInNewContext(sandbox)
  return sandbox.module.exports
}

test('Mac Gateway launch environment points to packaged Python and keeps user data outside the app', () => {
  const resources = '/Applications/小小办公室.app/Contents/Resources/office'
  const home = '/Users/owner/Library/Application Support/SmallOffice/office/state'
  const gateway = gatewayModule('darwin', {
    OFFICE_PACKAGED_RESOURCES: resources, DEER_FLOW_HOME: home,
    DEER_FLOW_PROJECT_ROOT: '/Users/owner/Library/Application Support/SmallOffice/office',
    DEER_FLOW_CONFIG_PATH: '/Users/owner/config.yaml', PATH: '/usr/bin:/bin',
  })
  const env = gateway.gatewayEnv('127.0.0.1', '8001')
  assert.equal(gateway.packagedPython, `${resources}/python/bin/python3`)
  assert.equal(env.PYTHONHOME, `${resources}/python`)
  assert.equal(env.PYTHONPATH, `${resources}/backend:${resources}/backend/packages/harness`)
  assert.equal(env.OFFICE_POWERSHELL_EXECUTABLE, `${resources}/powershell/pwsh`)
  assert.equal(env.DEER_FLOW_HOME, home)
  assert.equal(env.DEER_FLOW_CONFIG_PATH, '/Users/owner/config.yaml')
  assert.match(env.PATH, /\/git\/bin:/)
  assert.equal(env.GATEWAY_PORT, '8001')
})

test('Mac development uses prepared runtimes without replacing an explicit PowerShell path', () => {
  const gateway = gatewayModule('darwin', { PATH: '/usr/bin', OFFICE_POWERSHELL_EXECUTABLE: '/custom/pwsh' })
  const env = gateway.gatewayEnv('127.0.0.1', '8001')
  assert.equal(gateway.packagedPython, null)
  assert.equal(env.PYTHONHOME, undefined)
  assert.equal(env.OFFICE_POWERSHELL_EXECUTABLE, '/custom/pwsh')
  assert.match(env.PATH, /^\/Users\/owner\/办公室\/output\/packaging\/resources\/python\/bin:/)
})

test('Windows Gateway keeps the extended Python home and existing development PATH', () => {
  const resources = 'C:\\Office\\resources'
  const packaged = gatewayModule('win32', { OFFICE_PACKAGED_RESOURCES: resources })
  assert.equal(packaged.packagedPython, `${resources}\\python\\python.exe`)
  assert.equal(packaged.gatewayEnv('127.0.0.1', '8001').PYTHONHOME, '\\\\?\\C:\\Office\\resources\\python')
  const dev = gatewayModule('win32', { Path: 'C:\\Windows' })
  assert.equal(dev.gatewayEnv('127.0.0.1', '8001').Path, 'C:\\Windows')
})

test('Windows release staging includes the runtime path dependency', () => {
  const prepare = fs.readFileSync(path.join(__dirname, 'packaging/prepare.py'), 'utf8')
  assert.match(prepare, /'runtime-paths\.cjs'/)
  assert.equal(require('./packaging/runtimes.macos.json').node.version, require('../package.json').engines.node)
})
