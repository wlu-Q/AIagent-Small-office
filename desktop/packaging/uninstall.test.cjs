const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const builder = require('./builder.cjs')

test('uninstaller targets the exact profile used by the packaged app', () => {
  const entry = fs.readFileSync(path.join(__dirname, '../packaged-entry.cjs'), 'utf8')
  const hook = fs.readFileSync(builder.nsis.include, 'utf8')
  assert.match(entry, /path\.join\(app\.getPath\('appData'\), 'SmallOffice'\)/)
  assert.match(hook, /!macro customUnInstall/)
  assert.match(hook, /\$\{ifNot\} \$\{isUpdated\}/)
  assert.match(hook, /SetShellVarContext current/)
  assert.deepEqual(hook.split(/\r?\n/).filter(line => /^\s*RMDir/.test(line)).map(line => line.trim()), [
    'RMDir /r "$APPDATA\\SmallOffice"',
  ])
  assert.match(hook, /\$\{if\} \$\{Errors\}[\s\S]*Abort/)
  assert.doesNotMatch(hook, /\$INSTDIR|project|workspace|DEER_FLOW_HOME/)
  assert.equal(builder.nsis.deleteAppDataOnUninstall, undefined)
})
