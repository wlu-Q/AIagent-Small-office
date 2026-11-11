const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../client/node_modules/typescript')
const filename = path.join(__dirname, '../client/src/components/office-chat/fileReferencePath.ts')
const exported = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: exported })
const { resolveFileReferencePath: resolve, normalizeFileReferencePath: normalize } = exported

test('Markdown links resolve from the containing directory in the same namespace', () => {
  for (const [base, link, expected] of [
    ['README.md', 'components.css', 'components.css'],
    ['README.md', './components.css', 'components.css'],
    ['docs/README.md', '../components.css', 'components.css'],
    ['docs/README.md', './images/logo.png', 'docs/images/logo.png'],
    ['D:\\project\\docs\\README.md', '..\\components.css', 'D:/project/components.css'],
    ['/mnt/office-workspaces/task/docs/README.md', '../style.css', '/mnt/office-workspaces/task/style.css'],
    ['/mnt/user-data/outputs/README.md', './style.css', '/mnt/user-data/outputs/style.css'],
    ['README.md', '../outside.txt', '../outside.txt'],
    ['README.md', 'D:/another/file.txt', 'D:/another/file.txt'],
  ]) assert.equal(resolve(link, base), expected)
})

test('equivalent links share a tab path while unresolved traversal is retained for server rejection', () => {
  assert.equal(normalize('.\\docs\\..\\style.css'), 'style.css')
  assert.equal(normalize('../../private.txt'), '../../private.txt')
  assert.equal(normalize('D:/../private.txt'), 'D:/../private.txt')
})
