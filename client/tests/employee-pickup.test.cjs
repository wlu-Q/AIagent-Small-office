const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const crypto = require('node:crypto')

function load(relativePath) {
  const exports = {}
  const source = fs.readFileSync(path.join(__dirname, '../src', relativePath), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { exports })
  return exports
}

const { EmployeePressGesture } = load('characters/EmployeePressGesture.ts')
const { EmployeeInteraction } = load('characters/EmployeeInteraction.ts')
const { sweepMovement } = load('utils/sweepMovement.ts')

function setup() {
  const jobs = new Set()
  const events = []
  let now = 0
  const schedule = (delay, callback) => {
    const job = { at: now + delay, callback }
    jobs.add(job)
    return () => jobs.delete(job)
  }
  const advance = duration => {
    const end = now + duration
    for (;;) {
      const next = [...jobs].filter(job => job.at <= end).sort((first, second) => first.at - second.at)[0]
      if (!next) break
      jobs.delete(next)
      now = next.at
      next.callback()
    }
    now = end
  }
  const interaction = new EmployeeInteraction({
    schedule, pause: () => events.push('pause'), resume: () => events.push('resume'),
    play: action => events.push(action), stand: () => events.push('stand'),
    highlight: () => {}, openChat: () => events.push('chat'), random: () => 0,
  })
  interaction.setEnabled(true)
  const gesture = new EmployeePressGesture({
    schedule, lift: () => { events.push('lift'); return interaction.beginHold() },
    drop: () => events.push('drop'), click: () => interaction.click(),
  })
  return { gesture, interaction, events, advance, jobs }
}

const press = { id: 0, x: 20, y: 30 }

test('short release jumps immediately; a second release opens chat once', () => {
  const { gesture, events, advance } = setup()
  gesture.press(press); advance(100); gesture.release(press, true)
  assert.equal(events.filter(event => event === 'jump').length, 1)
  gesture.press(press); advance(100); gesture.release(press, true)
  assert.equal(events.filter(event => event === 'chat').length, 1)
  advance(500)
  assert.equal(events.includes('lift'), false)
})

test('Phaser-style prototype coordinate getters are copied explicitly', () => {
  const { gesture, events, advance } = setup()
  const pointer = Object.create({ get x() { return 20 }, get y() { return 30 } })
  pointer.id = 0
  gesture.press(pointer); advance(80); gesture.release(pointer, true)
  assert.equal(events.filter(event => event === 'jump').length, 1)
  const origin = Object.create({ get x() { return 10 }, get y() { return 50 } })
  const result = sweepMovement(origin, { x: 20, y: 60 }, agent, bounds, [])
  assert.equal(result.x, 20)
  assert.equal(result.y, 60)
})

test('400ms lifts once, survives projection refresh / pointer exit, and suppresses drag release clicks', () => {
  const { gesture, interaction, events, advance } = setup()
  gesture.press(press); advance(399)
  assert.equal(gesture.dragging, false)
  advance(1)
  assert.equal(gesture.dragging, true)
  interaction.leave(); interaction.animationComplete(); interaction.click()
  interaction.setEnabled(true); advance(2500)
  assert.equal(interaction.holding, true)
  assert.equal(interaction.active, true)
  gesture.move({ ...press, x: 400 }); gesture.release({ ...press, x: 400 }, false)
  assert.equal(events.filter(event => event === 'drop').length, 1)
  assert.equal(events.includes('jump') || events.includes('chat'), false)
  interaction.endHold(); advance(1499)
  assert.equal(interaction.active, true)
  advance(1)
  assert.equal(interaction.active, false)
})

test('movement over 5px before long press cancels both pickup and click', () => {
  const { gesture, events, advance } = setup()
  gesture.press(press); advance(150); gesture.move({ ...press, x: 26 }); advance(500)
  gesture.release(press, true)
  assert.deepEqual(events, [])
})

test('foreign pointers and duplicate downs cannot replace the active press', () => {
  const { gesture, events, advance } = setup()
  gesture.press(press); gesture.press({ id: 1, x: 400, y: 50 })
  gesture.move({ id: 1, x: 500, y: 50 }); gesture.release({ id: 1, x: 500, y: 50 }, true)
  advance(400)
  assert.equal(gesture.dragging, true)
  assert.equal(events.filter(event => event === 'lift').length, 1)
})

test('work / disabled / cancellation clear hold and timers without spurious chat', () => {
  for (const elapsed of [100, 450]) {
    const { gesture, interaction, events, advance, jobs } = setup()
    gesture.press(press); advance(elapsed)
    gesture.cancel(); interaction.setEnabled(false); advance(2000)
    gesture.release(press, true); interaction.click(); interaction.enter()
    assert.equal(interaction.holding || interaction.active || gesture.dragging, false)
    assert.equal(jobs.size, 0)
    assert.equal(events.includes('jump') || events.includes('chat'), false)
  }
})

test('pickup clears the previous double-click window; hovered landing stays paused', () => {
  const { gesture, interaction, events, advance } = setup()
  interaction.enter(); interaction.click()
  gesture.press(press); advance(400); gesture.release(press, true)
  interaction.endHold(); advance(1600)
  assert.equal(interaction.active, true)
  assert.equal(interaction.holding, false)
  interaction.click()
  assert.equal(events.includes('chat'), false)
})

const bounds = { x: -100, y: 0, width: 600, height: 400 }
const agent = { width: 16, height: 10, offsetY: 19 }

test('swept footprint stops before a thin wall even on a huge pointer movement', () => {
  const wall = { x: 100, y: 0, width: 1, height: 400 }
  const result = sweepMovement({ x: 20, y: 80 }, { x: 10000, y: 80 }, agent, bounds, [wall])
  assert.equal(result.collided, true)
  assert.ok(result.x <= 92 && result.x > 91.99)
  assert.equal(result.y, 80)
})

test('oblique contact slides along furniture, corners stop both axes, and moving away is free', () => {
  const desk = { x: 100, y: 100, width: 100, height: 100 }
  const result = sweepMovement({ x: 50, y: 100 }, { x: 150, y: 150 }, agent, bounds, [desk])
  assert.ok(result.x < 92 && result.x > 91.99)
  assert.ok(Math.abs(result.y - 150) < .01)
  const away = sweepMovement(result, { x: 20, y: 150 }, agent, bounds, [desk])
  assert.equal(away.x, 20)
  assert.equal(away.collided, false)
  const corner = sweepMovement({ x: 50, y: 34 }, { x: 150, y: 134 }, agent, bounds, [desk])
  assert.ok(corner.x <= 92 && corner.y <= 76)
})

test('world boundaries use the full footprint and negative office origin', () => {
  for (const target of [{ x: -1000, y: -1000 }, { x: 5000, y: 5000 }]) {
    const result = sweepMovement({ x: 10, y: 50 }, target, agent, bounds, [])
    assert.ok(result.x >= -92 && result.x <= 492)
    assert.ok(result.y >= -14 && result.y <= 376)
    assert.equal(result.collided, true)
  }
})

test('contact remains stable over repeated collision frames, without tunneling or drifting', () => {
  const obstacles = [{ x: 100, y: 0, width: 1, height: 400 }, { x: -100, y: 250, width: 201, height: 1 }]
  let position = { x: 20, y: 80 }
  for (let index = 0; index < 1000; index++) {
    position = sweepMovement(position, { x: 1000, y: 1000 }, agent, bounds, obstacles)
    assert.ok(position.x <= 92 && position.y <= 226)
  }
  assert.ok(position.x > 91.99 && position.y > 225.99)
})

test('all nine pickup sets keep approved timing, anchors and source identity', () => {
  const directory = path.join(__dirname, '../public/assets/character')
  const characters = load('characters/additionalCharacterTextures.ts').additionalCharacterTextures
  assert.equal(characters.length, 9)
  for (const character of characters) {
    const asset = JSON.parse(fs.readFileSync(path.join(directory, `pickup/${character}/frames.json`), 'utf8'))
    assert.equal(asset.sourceSha256, crypto.createHash('sha256').update(fs.readFileSync(path.join(directory, `${character}.png`))).digest('hex'), character)
    assert.equal(asset.frameRate, 20)
    assert.equal(asset.frameCount, 48)
    assert.equal(asset.scale, .5)
    assert.equal(asset.anchorX, 84)
    assert.equal(asset.anchorY, 122)
    assert.equal(asset.frames.length, 48)
    assert.ok(asset.frames.every(frame => frame.source[2] === 160 && frame.source[3] === 172 && frame.contentTop >= 0 && frame.contentTop < asset.anchorY))
    if (character !== 'ethan') {
      for (const layer of ['body', 'effects']) {
        assert.equal(asset.imageSha256[layer], crypto.createHash('sha256').update(fs.readFileSync(path.join(directory, `pickup/${character}/${layer}.png`))).digest('hex'), `${character}/${layer}`)
      }
    }
  }
})
