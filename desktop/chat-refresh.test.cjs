const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('../client/node_modules/typescript')

function load(relative) {
  const filename = path.join(__dirname, '..', relative)
  const exports = {}
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { exports, setTimeout, clearTimeout }, { filename })
  return exports
}
const { EmployeeProjection } = load('client/src/services/office/employeeProjection.ts')
const { RefreshTasks } = load('client/src/components/office-chat/refreshTasks.ts')
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('canvas, chat and editor share one request and the recent successful projection', async () => {
  const response = deferred()
  const publications = []
  let calls = 0
  let now = 100
  const cache = new EmployeeProjection(() => { calls++; return response.promise }, next => publications.push(next), () => now)
  const canvas = cache.read(), chat = cache.read(), editor = cache.read(true)
  await tick()
  assert.equal(calls, 1)
  const employees = [{ id: 'front', state: 'working' }]
  response.resolve(employees)
  assert.deepEqual(await Promise.all([canvas, chat, editor]), [employees, employees, employees])
  now += 999
  assert.equal(await cache.read(), employees)
  assert.equal(calls, 1)
  assert.equal(publications.length, 1)
})

test('a pre-mutation request cannot publish or return an obsolete idle employee', async () => {
  const old = deferred(), fresh = deferred()
  const publications = []
  let calls = 0
  const cache = new EmployeeProjection(() => ++calls === 1 ? old.promise : fresh.promise, next => publications.push(next))
  const first = cache.read()
  await tick()
  cache.invalidate()
  const second = cache.read()
  await tick()
  old.resolve([{ id: 'front', state: 'idle' }])
  await tick()
  assert.equal(publications.length, 0)
  const working = [{ id: 'front', state: 'working' }]
  fresh.resolve(working)
  assert.equal(await first, working)
  assert.equal(await second, working)
  assert.deepEqual(publications, [working])
  assert.equal(calls, 2)
})

test('failed employee refresh rejects instead of presenting cached idle as freshly verified', async () => {
  let fail = false, now = 1
  const publications = []
  const cache = new EmployeeProjection(async () => {
    if (fail) throw new Error('offline')
    return [{ id: 'front', state: 'idle' }]
  }, next => publications.push(next), () => now)
  await cache.read()
  now += 1001; fail = true
  await assert.rejects(cache.read(), /offline/)
  await assert.rejects(cache.read(), /offline/)
  assert.equal(publications.length, 1)
  fail = false
  await cache.read()
  assert.equal(publications.length, 2)
})

test('slow history and failing queue do not block other Runs or overlap their own requests', async t => {
  const tasks = new RefreshTasks(10)
  t.after(() => tasks.clear())
  const history = deferred()
  let historyCalls = 0, eventUpdates = 0, queueErrors = 0
  tasks.ensure('messages', () => { historyCalls++; return history.promise }, () => {}, () => {})
  tasks.ensure('queue', async () => { throw new Error('offline') }, () => assert.fail('failed queue is not empty queue'), () => queueErrors++)
  tasks.ensure('run-back', async () => 'step', () => eventUpdates++, () => {})
  tasks.ensure('messages', () => { historyCalls++; return history.promise }, () => {}, () => {})
  await delay(65)
  assert.equal(historyCalls, 1)
  assert.ok(eventUpdates >= 3)
  assert.ok(queueErrors >= 1)
  tasks.clear()
  history.resolve([])
  await tick()
})

test('switching conversation discards late success and failure callbacks', async () => {
  const tasks = new RefreshTasks(10)
  const oldSuccess = deferred(), oldFailure = deferred()
  const updates = []
  tasks.ensure('status', () => oldSuccess.promise, value => updates.push(value), () => updates.push('old error'))
  tasks.ensure('events', () => oldFailure.promise, value => updates.push(value), () => updates.push('old error'))
  tasks.clear()
  oldSuccess.resolve('other conversation')
  oldFailure.reject(new Error('late failure'))
  await tick()
  assert.deepEqual(updates, [])
})

test('failed lane retries independently and can recover without losing its owner', async t => {
  const tasks = new RefreshTasks(5)
  t.after(() => tasks.clear())
  let calls = 0, errors = 0
  const recovered = deferred()
  tasks.ensure('events:front', async () => {
    if (++calls === 1) throw new Error('temporary')
    return { runId: 'front', seq: 2 }
  }, result => { tasks.remove('events:front'); recovered.resolve(result) }, () => errors++)
  assert.deepEqual(await recovered.promise, { runId: 'front', seq: 2 })
  assert.equal(errors, 1)
  assert.equal(calls, 2)
})

test('queue invalidation starts a fresh read and discards an older empty response', async t => {
  const tasks = new RefreshTasks(1000)
  t.after(() => tasks.clear())
  const stale = deferred(), fresh = deferred()
  const updates = []
  tasks.ensure('queue', () => stale.promise, value => updates.push(value), () => assert.fail('old read failed'))
  tasks.remove('queue')
  tasks.ensure('queue', () => fresh.promise, value => updates.push(value), () => assert.fail('new read failed'))
  fresh.resolve(['queued-message'])
  await tick()
  stale.resolve([])
  await tick()
  assert.deepEqual(updates, [['queued-message']])
})
