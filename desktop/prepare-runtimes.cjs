const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { developmentPython } = require('./runtime-paths.cjs')

const root = path.resolve(__dirname, '..')
const result = spawnSync(developmentPython(path.join(root, 'backend')),
  [path.join(__dirname, 'packaging/prepare-runtimes.py'), ...process.argv.slice(2)],
  { cwd: root, stdio: 'inherit', windowsHide: true })
if (result.error) {
  console.error(`无法准备运行时：${result.error.message}；请先在 backend 运行 uv sync --locked`)
}
process.exitCode = result.status ?? 1
