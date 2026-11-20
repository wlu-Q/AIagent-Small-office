const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const { PNG } = require('pngjs')
const manifest = require('../public/assets/workstations/manifest.json')

const model = {}
const seats = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/scenes/employeeWorkstations.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: seats })
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/workstations/workstationModel.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, { exports: model, require: () => ({ default: manifest }) })

test('desk decoration is stable across reopen, varied across workstations, and has one item per slot', () => {
  const layouts = seats.listEmployeeWorkstations().map(seat => model.createDeskLayout(seat.id, 2))
  assert.equal(JSON.stringify(layouts[3]), JSON.stringify(model.createDeskLayout('workstation-04', 2)))
  assert.ok(new Set(layouts.map(layout => JSON.stringify(layout))).size >= 8)
  assert.equal(new Set(layouts.map(layout => layout.tower)).size, 2)
  const accessories = new Set(layouts.map(layout => layout.props[2]))
  assert.ok(accessories.has('tissueBox') && accessories.has('deskClock') && accessories.has('radio'))
  for (const prop of ['deskLamp', 'cactus', 'lemonTea']) assert.ok(layouts.some(layout => layout.props.includes(prop)), prop)
  assert.ok(!accessories.has('notebook') && !accessories.has('books'))
  assert.equal(manifest.props.notebook, undefined)
  assert.equal(manifest.props.books, undefined)
  assert.equal(fs.existsSync(path.join(__dirname, '../public/assets/workstations/books.png')), false)
  for (const layout of layouts) {
    assert.equal(layout.props.length, 3)
    assert.equal(new Set(layout.props).size, 3)
    assert.ok(['plant', 'flowers', 'deskLamp', 'cactus'].includes(layout.props[0]))
    assert.ok(['coffee', 'water', 'lemonTea'].includes(layout.props[1]))
  }
})

test('work GIF selection can reach every clip but never immediately repeats', () => {
  for (const previous of [undefined, ...model.WORK_SCREEN_CLIPS]) {
    const candidates = model.WORK_SCREEN_CLIPS.filter(name => name !== previous)
    const results = new Set()
    for (let index = 0; index < candidates.length; index++) {
      const next = model.nextScreenClip(previous, () => (index + 0.5) / candidates.length)
      assert.notEqual(next, previous)
      results.add(next)
    }
    assert.deepEqual(results, new Set(candidates))
  }
  assert.equal(model.WORK_SCREEN_CLIPS.length, 9)
})

test('working requires a bound employee physically at the desk; idle, disabled, unbound and returning never operate', () => {
  for (const state of ['idle', 'disabled', 'unbound', 'working']) {
    for (const atDesk of [true, false]) {
      for (const employeeId of [undefined, 'employee-1']) {
        assert.equal(model.isOperating({ state, atDesk, employeeId }), Boolean(employeeId && state === 'working' && atDesk))
      }
    }
  }
})

test('six hand sets bind deterministically to character appearance, never to employee name or random choice', () => {
  assert.equal(Object.keys(manifest.hands).length, 6)
  const characters = {}
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/characters/additionalCharacterTextures.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { exports: characters })
  for (const texture of characters.additionalMaleCharacterTextures) assert.ok(['navy', 'shirt', 'hoodie'].includes(model.handStyleForCharacter(texture)))
  for (const texture of characters.additionalFemaleCharacterTextures) assert.ok(['blouse', 'cardigan', 'suit'].includes(model.handStyleForCharacter(texture)))
  assert.equal(new Set(characters.additionalCharacterTextures.map(model.handStyleForCharacter)).size, 6)
  assert.equal(model.handStyleForCharacter('ethan'), 'navy')
  assert.equal(model.handStyleForCharacter(), undefined)
  assert.equal(model.handStyleForCharacter('unsupported'), undefined)
  assert.equal(model.handStyleForCharacter('toString'), undefined)
})

test('all workstation sides have independent IDs and can only be reached from their own side', () => {
  const workstations = seats.listEmployeeWorkstations()
  assert.equal(workstations.length, 24)
  assert.equal(new Set(workstations.map(seat => seat.id)).size, 24)
  assert.equal(new Set(workstations.map(seat => `${seat.x}:${seat.y}`)).size, 24)
  const lowerCoordinates = [[-48, 320], [-144, 320], [-240, 320], [-48, 544], [-144, 544], [-240, 544], [1008, 544], [1104, 544], [1200, 544], [1008, 800], [1104, 800], [1200, 800]]
  for (const [index, [x, y]] of lowerCoordinates.entries()) {
    const lower = seats.getEmployeeWorkstation(`workstation-${String(index + 1).padStart(2, '0')}`)
    const upper = seats.getEmployeeWorkstation(`workstation-${index + 13}`)
    assert.equal(JSON.stringify(lower), JSON.stringify({ id: lower.id, x, y, direction: 'up' }))
    assert.equal(JSON.stringify(upper), JSON.stringify({ id: upper.id, x, y: y - 96, direction: 'down' }))
    for (const seat of [lower, upper]) {
      assert.equal(seats.canReachWorkstation(seat, { x, y: seat.y + 3 }), true)
      const towardDesk = seat.direction === 'up' ? -1 : 1
      for (const horizontal of [-56, 56]) {
        assert.equal(seats.canReachWorkstation(seat, { x: x + horizontal, y: seat.y + 32 * towardDesk }), true)
      }
      assert.equal(seats.canReachWorkstation(seat, { x, y: seat.y + 48 * towardDesk }), false)
      assert.equal(seats.canReachWorkstation(seat, { x: x + 96, y: seat.y }), false)
      assert.equal(seats.canReachWorkstation(seat, { x, y: seat.y + 100 }), false)
    }
    assert.equal(seats.canReachWorkstation(lower, upper), false)
    assert.equal(seats.canReachWorkstation(upper, lower), false)
  }
})

test('Computer uses the same R entry and side-aware reach rule for both rows', () => {
  const exported = {}
  class Item {
    clearDialogBox() { this.dialog = undefined }
    setDialogBox(key) { this.dialog = key }
  }
  const source = fs.readFileSync(path.join(__dirname, '../src/items/Computer.ts'), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { exports: exported, require: name => name === './Item' ? { default: Item } : name.includes('employeeWorkstations') ? seats : { ItemType: { COMPUTER: 1 } } })
  const computer = new exported.default()
  computer.onOverlapDialog()
  assert.equal(computer.dialog, 'ui.press_r_to_view_workstation')
  let opened = 0
  computer.openPreview = () => { opened++; return true }
  computer.onOverlapDialog()
  assert.equal(computer.viewWorkstation(), true)
  assert.equal(opened, 1)
  assert.equal(computer.dialog, 'ui.press_r_to_view_workstation')
  computer.openPreview = () => false
  assert.equal(computer.viewWorkstation(), false)
  for (const workstation of seats.listEmployeeWorkstations()) {
    computer.workstation = workstation
    computer.height = 64
    assert.equal(computer.dialogOffsetY, workstation.direction === 'up' ? 32 : -32)
    assert.equal(computer.canInteractFrom(workstation), true)
    assert.equal(computer.canInteractFrom({ x: workstation.x + 100, y: workstation.y }), false)
  }
})

test('hands include typing, reach, moving and clicking mouse, pause and return without teleporting', () => {
  const phases = new Set()
  let clicks = 0
  let previous = model.handPose(0)
  for (let time = 16; time <= model.HAND_CYCLE_MS * 2; time += 16) {
    const pose = model.handPose(time)
    phases.add(pose.phase)
    if (pose.click) clicks++
    assert.ok(Math.abs(pose.rightX - previous.rightX) <= 4, `horizontal hand jump at ${time}`)
    assert.ok(Math.abs(pose.rightY - previous.rightY) <= 2, `vertical hand jump at ${time}`)
    if (pose.phase === 'mouse') {
      assert.equal(pose.mouseX, pose.rightX)
      assert.ok(Math.abs(pose.mouseY - pose.rightY) <= 0.5, 'only finger pressure may offset the mouse grip')
      if (pose.click) {
        const next = model.handPose(time + 1)
        assert.equal(pose.mouseX, next.mouseX, 'mouse must stop before clicking')
        assert.equal(pose.mouseY, next.mouseY)
      }
    }
    previous = pose
  }
  assert.equal(phases.size, 5)
  assert.ok(clicks > 0)
})

test('typing is slightly quicker, wrists press and rebound, and transfers ease at both ends', () => {
  assert.equal(model.KEY_STROKE_MS, 140)
  assert.ok(model.HAND_CYCLE_MS < 9600 && model.HAND_CYCLE_MS >= 8000)
  for (const [start, duration] of [[2800, 700], [7400, 1000]]) {
    const first = Math.abs(model.handPose(start + 16).rightX - model.handPose(start).rightX)
    const middle = Math.abs(model.handPose(start + duration / 2 + 16).rightX - model.handPose(start + duration / 2).rightX)
    const last = Math.abs(model.handPose(start + duration).rightX - model.handPose(start + duration - 16).rightX)
    assert.ok(first < middle / 5 && last < middle / 5)
  }
  assert.ok(model.handPose(380).leftY > model.handPose(280).leftY)
  assert.ok(model.handPose(520).leftY < model.handPose(380).leftY)
  for (const time of [2800, 3500, 6300, 7400, 8400]) {
    const before = model.handPose(time - 0.01)
    const after = model.handPose(time)
    for (const axis of ['leftX', 'leftY', 'rightX', 'rightY', 'mouseX', 'mouseY']) assert.ok(Math.abs(before[axis] - after[axis]) < 0.01, `${axis} discontinuity at ${time}`)
  }
})

test('all work screen assets are actual multi-frame looping GIFs with consistent geometry and timing', () => {
  for (const name of model.WORK_SCREEN_CLIPS) {
    const data = fs.readFileSync(path.join(__dirname, `../public/assets/workstations/screens/${name}.gif`))
    assert.equal(data.toString('ascii', 0, 6), 'GIF89a')
    assert.equal(data.readUInt16LE(6), manifest.screenFrames.width)
    assert.equal(data.readUInt16LE(8), manifest.screenFrames.height)
    let offset = 13 + ((data[10] & 128) ? 3 * 2 ** ((data[10] & 7) + 1) : 0)
    let frames = 0
    let controls = 0
    const skipBlocks = () => {
      while (data[offset]) { const length = data[offset]; offset += length + 1 }
      offset++
    }
    while (data[offset] !== 0x3b) {
      const marker = data[offset++]
      if (marker === 0x21) {
        const type = data[offset++]
        if (type === 0xf9) {
          assert.equal(data.readUInt16LE(offset + 2), manifest.screenFrames.delay / 10)
          controls++
        }
        skipBlocks()
      } else {
        assert.equal(marker, 0x2c)
        const flags = data[offset + 8]
        offset += 9
        if (flags & 128) offset += 3 * 2 ** ((flags & 7) + 1)
        offset++
        skipBlocks()
        frames++
      }
      assert.ok(offset < data.length)
    }
    assert.equal(frames, manifest.screenFrames.count)
    assert.equal(controls, manifest.screenFrames.count)
    assert.ok(data.includes(Buffer.from('NETSCAPE2.0')))
  }
})

test('renderer keeps bound desktop artwork and blacks out the unbound screen without props or hands', () => {
  const drawing = {}
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/workstations/drawWorkstation.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { exports: drawing, require: name => name === './workstationModel' ? model : { workstationArtworkManifest: manifest } })
  const draw = (occupied, operating, elapsed, handStyle = 'navy', layout = model.createDeskLayout('workstation-04', 2)) => {
    const operations = []
    const context = {
      imageSmoothingEnabled: true, clearRect() {},
      drawImage(...coordinates) { operations.push(coordinates) },
      fillRect(...coordinates) { operations.push(['black-screen', ...coordinates]); assert.equal(this.fillStyle, '#000000') },
    }
    drawing.drawWorkstation(context, { base: 'base', props: 'props', wallpaper: 'wallpaper', hands: 'hands', handStyle, screens: 'screens' }, layout, occupied, operating, elapsed)
    assert.equal(context.imageSmoothingEnabled, false)
    assert.ok(operations.every(([, ...coordinates]) => coordinates.every(Number.isInteger)))
    return operations
  }
  const empty = draw(false, false, 0)
  const idle = draw(true, false, 0)
  const working = draw(true, true, 0)
  assert.deepEqual(empty.find(operation => operation[0] === 'black-screen'), ['black-screen', model.SCREEN_RECT.x, model.SCREEN_RECT.y, model.SCREEN_RECT.width, model.SCREEN_RECT.height])
  assert.equal(empty.filter(operation => operation[0] === 'screens').length, 0)
  assert.equal(empty.filter(operation => operation[0] === 'wallpaper').length, 0)
  assert.equal(idle.filter(operation => operation[0] === 'wallpaper').length, 1)
  assert.equal(idle.filter(operation => operation[0] === 'black-screen').length, 0)
  assert.equal(working.filter(operation => operation[0] === 'black-screen').length, 0)
  assert.ok(idle.length > empty.length)
  assert.ok(working.length > idle.length)
  assert.equal(JSON.stringify(draw(false, false, 7000)), JSON.stringify(empty))
  assert.notEqual(JSON.stringify(draw(true, true, 5200)), JSON.stringify(working))
  assert.equal(working.filter(operation => operation[0] === 'hands').length, 2)
  assert.equal(empty.filter(operation => operation[0] === 'hands').length, 0)
  assert.deepEqual(working[0], ['base', 0, 0])
  for (const handStyle of Object.keys(manifest.hands)) {
    const frame = draw(true, true, 4000, handStyle)
    assert.equal(frame.filter(operation => operation[0] === 'hands').length, 2)
  }
  for (const accessory of ['tissueBox', 'deskClock', 'radio']) {
    for (const tower of [true, false]) {
      const frame = draw(true, false, 0, 'navy', { tower, props: ['plant', 'coffee', accessory] })
      const source = manifest.props[accessory]
      const item = frame.find(operation => operation[0] === 'props' && operation[1] === source.x && operation[2] === source.y)
      assert.ok(item, `${accessory} must use the new Image sprite`)
      const [, , , , , left, top, width, height] = item
      assert.ok(left >= 0 && left + width <= model.WORKSTATION_WIDTH, 'accessory must not clip at desk edge')
      assert.ok(top + height > 534 && top + height < model.WORKSTATION_HEIGHT, 'accessory base must rest on wood')
      assert.equal(height, { tissueBox: 160, deskClock: 180, radio: 185 }[accessory])
    }
  }
})

test('desktop shortcut icons are separate square images with transparent backgrounds', () => {
  for (const name of ['memory', 'records', 'recycle']) {
    const image = PNG.sync.read(fs.readFileSync(path.join(__dirname, `../public/assets/workstations/${name}-icon.png`)))
    assert.equal(image.width, image.height)
    assert.equal(image.data[3], 0)
    let transparent = 0
    for (let offset = 3; offset < image.data.length; offset += 4) if (image.data[offset] === 0) transparent++
    assert.ok(transparent / (image.width * image.height) > 0.15)
    assert.ok(transparent / (image.width * image.height) < 0.8)
  }
  assert.equal(fs.existsSync(path.join(__dirname, '../public/assets/workstations/desktop-icons.png')), false)
})

test('generated atlases have real transparency and every crop stays inside its source image', () => {
  assert.equal(manifest.generator, 'image_gen')
  const read = name => PNG.sync.read(fs.readFileSync(path.join(__dirname, `../public/assets/workstations/${name}.png`)))
  const base = read('desk-base')
  assert.equal(base.width, model.WORKSTATION_WIDTH)
  assert.equal(base.height, model.WORKSTATION_HEIGHT)
  const atlases = [['props', manifest.props], ...Object.values(manifest.hands).map(atlas => [atlas.file, atlas.frames])]
  for (const [name, crops] of atlases) {
    const image = read(name)
    let transparent = 0
    for (let offset = 3; offset < image.data.length; offset += 4) if (image.data[offset] === 0) transparent++
    assert.ok(transparent / (image.width * image.height) > 0.3)
    for (const crop of Object.values(crops)) {
      assert.ok(crop.x >= 0 && crop.y >= 0)
      assert.ok(crop.x + crop.width <= image.width && crop.y + crop.height <= image.height)
      if (name.startsWith('hands')) assert.ok(crop.anchorX > 0 && crop.anchorX < crop.width && crop.anchorY < crop.height)
    }
  }
})
