const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const asar = require('@electron/asar')

const root = path.resolve(__dirname, '../..')
const staged = path.join(root, 'output/packaging/app')
const resources = path.join(root, 'output/packaging/resources')
const packaged = path.join(root, 'dist/win-unpacked/resources')
let checked = 0

function verifyDirectory(directory, readPackaged) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === '.gitkeep' || entry.name.endsWith('.pyc')) continue
    const source = path.join(directory, entry.name)
    if (entry.isDirectory()) verifyDirectory(source, readPackaged)
    else {
      assert.ok(fs.readFileSync(source).equals(readPackaged(source)), `Packaged file differs: ${source}`)
      checked += 1
    }
  }
}

verifyDirectory(staged, source => asar.extractFile(path.join(packaged, 'app.asar'), path.relative(staged, source)))
for (const directory of ['backend', 'skills', 'defaults']) {
  verifyDirectory(path.join(resources, directory), source => fs.readFileSync(path.join(packaged, 'office', path.relative(resources, source))))
}
for (const relative of ['initialize.py', 'release-manifest.json', 'python/python.exe', 'node/node.exe', 'git/cmd/git.exe', 'powershell/pwsh.exe']) {
  assert.ok(fs.readFileSync(path.join(resources, relative)).equals(fs.readFileSync(path.join(packaged, 'office', relative))), relative)
}
console.log(`Packaged application matches staged code and assets: ${checked} files, bundled entry points verified.`)
