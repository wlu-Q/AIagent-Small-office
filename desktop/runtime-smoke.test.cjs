const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { runtimePaths, runtimeEnvironment } = require('./runtime-paths.cjs')
const { gatewayEnv } = require('./gateway-env.cjs')

const root = path.resolve(__dirname, '..')
const resources = path.join(root, 'output/packaging/resources')
const runtimes = runtimePaths(resources)
const env = runtimeEnvironment(resources, {
  ...gatewayEnv('127.0.0.1', '8001'),
  PYTHONHOME: path.toNamespacedPath(path.join(resources, 'python')),
  PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1',
})

function execute(executable, args, cwd = root, environment = env) {
  const result = spawnSync(executable, args, { cwd, env: environment, encoding: 'utf8', timeout: 30000, windowsHide: true })
  assert.ifError(result.error)
  assert.equal(result.status, 0, result.stderr)
  return result.stdout.trim()
}

test('native bundled Python imports the Gateway dependencies and LanceDB', () => {
  const result = JSON.parse(execute(runtimes.python, ['-c',
    'import json, platform, uvicorn, lancedb, langgraph, yaml, fastapi, deerflow; print(json.dumps({"machine": platform.machine(), "system": platform.system()}))']))
  assert.equal(result.system, process.platform === 'win32' ? 'Windows' : 'Darwin')
  assert.ok((process.platform === 'win32' ? ['AMD64', 'x86_64'] : ['arm64', 'aarch64']).includes(result.machine))
})

test('native bundled Node and PowerShell run and preserve a nonzero native exit code', () => {
  const node = JSON.parse(execute(runtimes.node, ['-p', 'JSON.stringify({platform:process.platform,arch:process.arch})']))
  assert.equal(node.platform, process.platform)
  assert.equal(node.arch, process.arch)
  const command = "node -e 'process.stdout.write(process.platform);process.exit(23)'; exit $LASTEXITCODE"
  const result = spawnSync(runtimes.powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command],
    { env, encoding: 'utf8', timeout: 30000, windowsHide: true })
  assert.ifError(result.error)
  assert.equal(result.status, 23, result.stderr)
  assert.equal(result.stdout, process.platform)
})

test('native bundled Git creates an isolated worktree without changing the original file', () => {
  const temporary = fs.mkdtempSync(path.join(fs.realpathSync.native(os.tmpdir()), 'office-runtime-中文 '))
  const repository = path.join(temporary, 'project')
  const workspace = path.join(temporary, 'worktree')
  fs.mkdirSync(repository)
  const gitConfig = path.join(temporary, 'empty.gitconfig')
  fs.writeFileSync(gitConfig, '')
  const gitEnv = { ...env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: gitConfig }
  for (const name of Object.keys(gitEnv)) {
    if (name.startsWith('GIT_') && !['GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL'].includes(name)) delete gitEnv[name]
  }
  const git = (...args) => execute(runtimes.git, ['-c', 'user.name=Office Runtime Test', '-c', 'user.email=runtime@example.invalid',
    '-c', 'commit.gpgsign=false', ...args], repository, gitEnv)
  try {
    git('init', '--template=')
    fs.writeFileSync(path.join(repository, 'example.txt'), 'baseline\n')
    git('add', 'example.txt')
    git('commit', '-m', 'runtime smoke baseline')
    git('worktree', 'add', '-b', 'runtime-smoke', workspace)
    assert.equal(fs.readFileSync(path.join(workspace, 'example.txt'), 'utf8'), 'baseline\n')
    fs.writeFileSync(path.join(workspace, 'example.txt'), 'isolated\n')
    assert.equal(fs.readFileSync(path.join(repository, 'example.txt'), 'utf8'), 'baseline\n')
    assert.equal(git('status', '--porcelain'), '')
    git('worktree', 'remove', '--force', workspace)
  } finally {
    assert.equal(path.dirname(temporary), fs.realpathSync.native(os.tmpdir()))
    assert.ok(path.basename(temporary).startsWith('office-runtime-'))
    fs.rmSync(temporary, { recursive: true, force: true })
  }
})
