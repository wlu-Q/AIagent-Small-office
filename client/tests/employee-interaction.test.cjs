const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const moduleExports = {}
const source = fs.readFileSync(path.join(__dirname, '../src/characters/EmployeeInteraction.ts'), 'utf8')
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: moduleExports })
const { EmployeeInteraction } = moduleExports

function setup(random = 0) {
  let now = 0
  const jobs = new Set()
  const events = []
  const controller = new EmployeeInteraction({
    schedule(delay, callback) {
      const job = { at: now + delay, callback }
      jobs.add(job)
      return () => jobs.delete(job)
    },
    pause: () => events.push('pause'), resume: () => events.push('resume'),
    play: action => events.push(action), stand: () => events.push('stand'),
    highlight: visible => events.push(visible ? 'highlight' : 'unhighlight'),
    openChat: () => events.push('chat'), random: () => random,
  })
  const advance = milliseconds => {
    const end = now + milliseconds
    for (;;) {
      const next = [...jobs].filter(job => job.at <= end).sort((a, b) => a.at - b.at)[0]
      if (!next) break
      now = next.at
      jobs.delete(next)
      next.callback()
    }
    now = end
  }
  controller.setEnabled(true)
  return { controller, advance, events, jobs }
}

test('hover chooses all four gestures; repeated hover/projection never restarts a gesture', () => {
  for (const [random, expected] of [[0, 'nod'], [.25, 'scratch'], [.5, 'clap'], [.75, 'stretch']]) {
    const { controller: c, events } = setup(random)
    c.enter(); c.setEnabled(true); c.enter(); c.animationComplete(); c.enter()
    assert.equal(events.filter(item => item === expected).length, 1)
    assert.equal(events.filter(item => item === 'pause').length, 1)
    assert.equal(c.active, true)
  }
})

test('leave waits exactly 1500ms; reentry cancels the old deadline without a new greeting', () => {
  const { controller: c, advance, events } = setup()
  c.enter(); c.leave(); advance(1499)
  assert.equal(c.active, true)
  c.enter(); advance(1)
  assert.equal(c.active, true)
  assert.equal(events.filter(item => item === 'nod').length, 1)
  c.leave(); advance(1499)
  assert.equal(events.includes('resume'), false)
  advance(1)
  assert.equal(c.active, false)
  assert.equal(events.filter(item => item === 'resume').length, 1)
})

test('single click jumps synchronously and cannot stack jumps after the double-click window', () => {
  const { controller: c, advance, events } = setup()
  c.enter(); c.click()
  assert.equal(events.includes('jump'), true)
  assert.equal(c.jumping, true)
  advance(301); c.click(); advance(400)
  assert.equal(events.filter(item => item === 'jump').length, 1)
  assert.equal(events.includes('chat'), false)
  c.animationComplete()
  assert.equal(c.jumping, false)
})

test('double click opens one private chat after the immediate hop, without starting another hop', () => {
  const { controller: c, advance, events } = setup()
  c.enter(); c.click(); advance(120); c.click(); advance(300)
  assert.equal(events.filter(item => item === 'chat').length, 1)
  assert.equal(events.filter(item => item === 'jump').length, 1)
  assert.equal(c.jumping, false)
  assert.equal(c.hovered, false)
  advance(1200)
  assert.equal(c.active, false)
})

test('working/disabled cancels the active hop, double-click window and leave timers', () => {
  const { controller: c, advance, events, jobs } = setup()
  c.enter(); c.click(); c.leave(); c.setEnabled(false)
  assert.equal(jobs.size, 0)
  c.animationComplete(); c.enter(); c.click(); advance(2000)
  assert.equal(events.filter(item => item === 'jump').length, 1)
  assert.equal(c.jumping, false)
  assert.equal(events.includes('chat'), false)
  assert.equal(events.includes('resume'), false)
  assert.equal(c.active, false)
  c.setEnabled(true); c.enter()
  assert.equal(c.active, true)
})

test('clicks on different employees do not combine into a double click', () => {
  const a = setup(), b = setup()
  a.controller.click(); b.controller.click(); a.advance(300); b.advance(300)
  assert.equal(a.events.includes('chat') || b.events.includes('chat'), false)
  assert.equal(a.events.includes('jump') && b.events.includes('jump'), true)
})

test('destroy while jumping leaves no callbacks that can resume the employee', () => {
  const { controller: c, advance, events, jobs } = setup()
  c.enter(); c.click(); advance(300); c.leave(); c.setEnabled(false)
  c.animationComplete(); advance(5000)
  assert.equal(jobs.size, 0)
  assert.equal(events.includes('resume'), false)
})
