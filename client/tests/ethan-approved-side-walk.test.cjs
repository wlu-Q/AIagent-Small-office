const { test } = require('node:test')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const { readFileSync } = require('node:fs')
const { createHash } = require('node:crypto')
const path = require('node:path')

const client = path.resolve(__dirname, '..')
const atlas = path.join(client, 'public/assets/character/ethan.png')
const approved = path.join(client, 'assets-source/employee-daily/ethan-side-walk.png')
const pixels = (file, filter) => execFileSync('ffmpeg', [
  '-hide_banner', '-loglevel', 'error', '-i', file, '-vf', filter,
  '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1',
])

test('all twelve lateral frames match the approved preview exactly', () => {
  for (const direction of [0, 1]) {
    for (let phase = 0; phase < 6; phase++) {
      const actual = pixels(atlas, `crop=96:114:${(phase + 1) * 96}:${direction * 228}`)
      const expected = pixels(approved, `crop=96:114:${phase * 96}:${direction * 114}`)
      assert.deepEqual(actual, expected, `direction ${direction}, phase ${phase} differs from approved artwork`)
    }
  }
})

test('the packed atlas hash matches the asset manifest', () => {
  const manifest = JSON.parse(readFileSync(path.join(client, 'assets-source/employee-daily/frames.json'), 'utf8'))
  assert.equal(createHash('sha256').update(readFileSync(atlas)).digest('hex'), manifest.ethan.atlasSha256)
})
