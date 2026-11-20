const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { PNG } = require('pngjs')
const { GIFEncoder, quantize, applyPalette } = require('gifenc')

const root = path.resolve(__dirname, '../public/assets/workstations')
const read = name => PNG.sync.read(fs.readFileSync(path.join(root, `${name}.png`)))

function regions(image, columns, rows, names, anchored = false) {
  return Object.fromEntries(names.map((name, index) => {
    const left = Math.floor(index % columns * image.width / columns)
    const right = Math.floor((index % columns + 1) * image.width / columns)
    const top = Math.floor(Math.floor(index / columns) * image.height / rows)
    const bottom = Math.floor((Math.floor(index / columns) + 1) * image.height / rows)
    let minX = right, minY = bottom, maxX = left, maxY = top
    for (let row = top; row < bottom; row++) {
      for (let column = left; column < right; column++) {
        if (image.data[(row * image.width + column) * 4 + 3] <= 64) continue
        minX = Math.min(minX, column); maxX = Math.max(maxX, column)
        minY = Math.min(minY, row); maxY = Math.max(maxY, row)
      }
    }
    assert.ok(maxX > minX && maxY > minY, `Empty sprite: ${name}`)
    const x = Math.max(left, minX - 2), y = Math.max(top, minY - 2)
    const width = Math.min(right, maxX + 3) - x, height = Math.min(bottom, maxY + 3) - y
    if (!anchored) return [name, { x, y, width, height }]
    const wrist = []
    for (let column = minX; column <= maxX; column++) {
      if (image.data[((maxY - 5) * image.width + column) * 4 + 3] > 128) wrist.push(column)
    }
    assert.ok(wrist.length > 10, `Missing wrist anchor: ${name}`)
    return [name, { x, y, width, height, anchorX: Math.round((wrist[0] + wrist.at(-1)) / 2) - x, anchorY: maxY - y }]
  }))
}

const base = read('desk-base')
const props = read('props')
const handFiles = { navy: 'hands', shirt: 'hands-shirt', hoodie: 'hands-hoodie', blouse: 'hands-blouse', cardigan: 'hands-cardigan', suit: 'hands-suit' }
const handNames = ['left0', 'left1', 'left2', 'left3', 'right0', 'right1', 'right2', 'right3']
const screens = read('screen-frames')
const frameWidth = screens.width / 3, frameHeight = screens.height / 4
assert.ok(Number.isInteger(frameWidth) && Number.isInteger(frameHeight))
const sequence = [0, 1, 2, 3, 2, 1]
const forward = [0, 1, 1, 2, 3, 3]
const clipSheets = [
  [screens, ['code', 'document', 'chart'], sequence],
  [read('../../../assets-source/workstations/typing'), ['web-search', 'code-typing', 'project-brief'], forward],
  [read('../../../assets-source/workstations/review'), ['web-browse', 'code-debug', 'meeting-notes'], forward],
]
const delay = 320
const manifest = {
  generator: 'image_gen',
  style: 'Approved warm-wood workstation reference; image layers only, no procedural artwork',
  width: base.width, height: base.height,
  screen: { x: 428, y: 82, width: 815, height: 377 },
  screenFrames: { width: frameWidth, height: frameHeight, count: sequence.length, delay, duration: sequence.length * delay,
    clips: clipSheets.flatMap(([, names]) => names) },
  props: regions(props, 5, 3, ['tower', 'plant', 'flowers', 'coffee', 'water', 'figurine', 'tissueBox', 'deskClock', 'pencils', 'mouse',
    'deskLamp', 'cactus', 'lemonTea', 'radio']),
  hands: Object.fromEntries(Object.entries(handFiles).map(([style, file]) => {
    const image = read(file)
    return [style, { file, scale: 0.78 * 1774 / image.width, frames: regions(image, 4, 2, handNames, true) }]
  })),
}
fs.mkdirSync(path.join(root, 'screens'), { recursive: true })
function writeClip(sheet, column, name, playback) {
  const frames = Array.from({ length: 4 }, (_, row) => {
    const pixels = new Uint8Array(frameWidth * frameHeight * 4)
    for (let offset = 0; offset < frameHeight; offset++) {
      const start = ((row * frameHeight + offset) * sheet.width + column * frameWidth) * 4
      pixels.set(sheet.data.subarray(start, start + frameWidth * 4), offset * frameWidth * 4)
    }
    return pixels
  })
  const combined = new Uint8Array(frameWidth * frameHeight * 4 * 4)
  frames.forEach((pixels, index) => combined.set(pixels, pixels.length * index))
  const palette = quantize(combined, 256)
  const indexed = frames.map(pixels => applyPalette(pixels, palette))
  const gif = GIFEncoder()
  for (const index of playback) gif.writeFrame(indexed[index], frameWidth, frameHeight, { palette, delay, repeat: 0 })
  gif.finish()
  fs.writeFileSync(path.join(root, 'screens', `${name}.gif`), gif.bytes())
  console.log(`${name}: ${sequence.length} Image-generated frames, ${manifest.screenFrames.duration}ms loop`)
}
for (const [sheet, names, playback] of clipSheets) {
  assert.equal(sheet.width, frameWidth * names.length)
  assert.equal(sheet.height, frameHeight * 4)
  assert.equal(playback.length, manifest.screenFrames.count)
  names.forEach((name, column) => writeClip(sheet, column, name, playback))
}
fs.writeFileSync(path.join(root, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
console.log('Sprite regions and wrist anchors saved; source PNGs unchanged.')
