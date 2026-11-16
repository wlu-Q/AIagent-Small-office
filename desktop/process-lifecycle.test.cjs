const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')

function load(platform, kill = () => {}) {
  const calls = []
  const sandbox = {
    module: { exports: {} },
    process: { platform, kill },
    require() { return { spawnSync: (...args) => { calls.push(args); return { status: 0 } } } },
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'process-lifecycle.cjs'), 'utf8'), sandbox)
  return { stop: sandbox.module.exports.stopOwnedProcess, calls }
}

function child() {
  return { pid: 1234, exitCode: null, signalCode: null, killed: false, kill() { this.killed = true } }
}

test('Windows retains taskkill tree termination', () => {
  const { stop, calls } = load('win32')
  stop(child(), { processGroup: true })
  assert.equal(calls[0][0], 'taskkill')
  assert.deepEqual(Array.from(calls[0][1]), ['/PID', '1234', '/T', '/F'])
})

test('Mac terminates only an explicitly owned detached group', () => {
  const killed = []
  const { stop } = load('darwin', (...args) => killed.push(args))
  const owned = child()
  stop(owned, { processGroup: true })
  assert.deepEqual(killed, [[-1234, 'SIGKILL']])
  assert.equal(owned.killed, false)
  const ordinary = child()
  stop(ordinary)
  assert.equal(ordinary.killed, true)
  assert.equal(killed.length, 1)
})

test('Finished or unowned children never cause a kill', () => {
  const { stop } = load('darwin', () => assert.fail('Unexpected kill'))
  for (const owned of [null, {}, { ...child(), exitCode: 0 }, { ...child(), signalCode: 'SIGTERM' }]) {
    stop(owned, { processGroup: true })
  }
})

test('Mac ignores an already-gone group but exposes permission errors', () => {
  load('darwin', () => { throw Object.assign(new Error('gone'), { code: 'ESRCH' }) }).stop(child(), { processGroup: true })
  const { stop } = load('darwin', () => { throw Object.assign(new Error('denied'), { code: 'EPERM' }) })
  assert.throws(() => stop(child(), { processGroup: true }), /denied/)
})
