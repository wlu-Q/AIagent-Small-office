const { execFileSync } = require('node:child_process')
const { mkdirSync, copyFileSync } = require('node:fs')
const path = require('node:path')

const client = path.resolve(__dirname, '..')
const input = path.resolve(process.argv[2] || path.join(client, 'public/assets/character/ethan.png'))
const output = path.resolve(process.argv[3] || input)
const source = path.join(client, 'assets-source/employee-daily/ethan-wide-walk.png')
const sideSource = path.join(client, 'assets-source/employee-daily/ethan-side-walk.png')
const work = path.join(client, 'output/ethan-stride/packed')
mkdirSync(work, { recursive: true })

const rows = [
  { sideRow: 0 },
  { center: 147, top: 445, height: 39, anchor: 48, upperPoses: [1, 6, 6, 4, 6, 6] },
  { sideRow: 1 },
  { center: 134, top: 959, height: 40, anchor: 48, upperPoses: [1, 6, 6, 4, 6, 6] },
]
const ffmpeg = (...args) => execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args])

for (const [row, config] of rows.entries()) {
  for (let column = 0; column < 8; column++) {
    const destination = path.join(work, `frame-${String(row * 8 + column).padStart(2, '0')}.png`)
    if (column === 0 || column === 7) {
      ffmpeg('-i', input, '-vf', `crop=96:114:${column * 96}:${row * 114}`, '-frames:v', '1', destination)
      continue
    }
    const phase = column - 1
    if ('sideRow' in config) {
      ffmpeg('-i', sideSource, '-vf', `crop=96:114:${phase * 96}:${config.sideRow * 114}`, '-frames:v', '1', destination)
      continue
    }
    const cropX = phase % 3 * 256 + config.center - 64
    const flip = phase >= 3 ? ',hflip' : ''
    const passing = phase % 3 === 2
    const legs = passing
      ? `[legSource]crop=96:18:576:${row * 114 + 94}${flip}[legs]`
      : `[1:v]crop=128:${config.height}:${cropX}:${config.top},scale=60:18:flags=neighbor,lut=a='if(lt(val,128),0,val)'${flip}[legs]`
    const filters = [
      passing ? '[0:v]split=3[headSource][bodySource][legSource]' : '[0:v]split[headSource][bodySource]',
      `[headSource]crop=96:76:576:${row * 114},pad=96:114:0:0:color=black@0[head]`,
      `[bodySource]crop=96:18:${config.upperPoses[phase] * 96}:${row * 114 + 76}[body]`,
      '[head][body]overlay=x=0:y=76:format=auto[upper]',
      legs,
      `[upper][legs]overlay=x=${passing ? 0 : config.anchor - 30}:y=94:format=auto[out]`,
    ].join(';')
    ffmpeg('-i', input, '-i', source, '-filter_complex', filters, '-map', '[out]', '-frames:v', '1', destination)
  }
}
const atlas = path.join(work, 'atlas.png')
ffmpeg('-i', path.join(work, 'frame-%02d.png'), '-vf', 'tile=8x4:nb_frames=32:color=black@0', '-frames:v', '1', atlas)
copyFileSync(atlas, output)
