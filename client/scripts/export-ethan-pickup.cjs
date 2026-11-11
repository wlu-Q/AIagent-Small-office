async page => {
  await page.setViewportSize({ width: 800, height: 520 })
  await page.goto('http://127.0.0.1:5179/')
  const setup = await page.evaluate(async () => {
    const source = new Image()
    source.src = '/assets/character/ethan.png'
    await source.decode()
    const createCanvas = (width, height) => {
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      canvas.getContext('2d').imageSmoothingEnabled = false
      return canvas
    }
    const original = createCanvas(96, 114)
    original.getContext('2d').drawImage(source, 0, 342, 96, 114, 0, 0, 96, 114)
    const standing = createCanvas(96, 114)
    standing.getContext('2d').drawImage(original, 0, 0)
    const face = original.getContext('2d')
    const standingPixels = face.getImageData(0, 0, 96, 114)
    const inkOffset = (62 * 96 + 38) * 4
    const ink = Array.from(standingPixels.data.slice(inkOffset, inkOffset + 4))
    const edited = face.getImageData(0, 0, 96, 114)
    for (const left of [35, 54]) {
      for (let vertical = 61; vertical <= 65; vertical++) {
        const skinOffset = (vertical * 96 + left) * 4
        const skin = standingPixels.data.slice(skinOffset, skinOffset + 4)
        for (let horizontal = left; horizontal < left + 8; horizontal++) {
          edited.data.set(skin, (vertical * 96 + horizontal) * 4)
        }
      }
    }
    for (let vertical = 0; vertical < 5; vertical++) {
      const inset = 2 - Math.abs(vertical - 2)
      for (const horizontal of [37 + inset, 59 - inset]) {
        for (let width = 0; width < 2; width++) edited.data.set(ink, ((61 + vertical) * 96 + horizontal + width) * 4)
      }
    }
    let changedPixels = 0
    for (let offset = 0; offset < edited.data.length; offset += 4) {
      if (edited.data.slice(offset, offset + 4).every((value, channel) => value === standingPixels.data[offset + channel])) continue
      const horizontal = offset / 4 % 96
      const vertical = Math.floor(offset / 4 / 96)
      if (vertical < 61 || vertical > 65 || !((horizontal >= 35 && horizontal <= 42) || (horizontal >= 54 && horizontal <= 61))) throw new Error('Pixels outside lenses changed')
      changedPixels++
    }
    face.putImageData(edited, 0, 0)
    const originalPixels = face.getImageData(0, 0, 96, 114).data
    const colorAt = (horizontal, vertical) => {
      const offset = (vertical * 96 + horizontal) * 4
      return `rgb(${originalPixels[offset]},${originalPixels[offset + 1]},${originalPixels[offset + 2]})`
    }
    const owner = (horizontal, vertical) => {
      if (vertical < 74) return 'head'
      if (vertical >= 82 && vertical < 94 && horizontal < 32) return 'leftArm'
      if (vertical >= 82 && vertical < 94 && horizontal >= 66) return 'rightArm'
      if (vertical >= 94 && vertical < 99 && horizontal < 30) return 'leftArm'
      if (vertical >= 94 && vertical < 99 && horizontal >= 66) return 'rightArm'
      if (vertical >= 99) return horizontal < 48 ? 'leftLeg' : 'rightLeg'
      return 'body'
    }
    const names = ['body', 'leftArm', 'rightArm', 'leftLeg', 'rightLeg', 'head']
    const parts = Object.fromEntries(names.map(name => {
      const canvas = createCanvas(96, 114)
      const pixels = canvas.getContext('2d').createImageData(96, 114)
      for (let vertical = 0; vertical < 114; vertical++) {
        for (let horizontal = 0; horizontal < 96; horizontal++) {
          if (owner(horizontal, vertical) !== name) continue
          const offset = (vertical * 96 + horizontal) * 4
          pixels.data.set(originalPixels.subarray(offset, offset + 4), offset)
        }
      }
      canvas.getContext('2d').putImageData(pixels, 0, 0)
      return [name, canvas]
    }))
    parts.hips = createCanvas(96, 114)
    parts.hips.getContext('2d').drawImage(original, 30, 96, 36, 3, 30, 96, 36, 3)
    parts.hips.getContext('2d').drawImage(original, 28, 99, 40, 3, 28, 99, 40, 3)
    const reconstruction = createCanvas(96, 114)
    for (const name of names) reconstruction.getContext('2d').drawImage(parts[name], 0, 0)
    const reconstructed = reconstruction.getContext('2d').getImageData(0, 0, 96, 114).data
    let neutralDifferences = 0
    for (let offset = 0; offset < originalPixels.length; offset++) {
      if (originalPixels[offset] !== reconstructed[offset]) neutralDifferences++
    }
    if (neutralDifferences) throw new Error(`Source partition mismatch: ${neutralDifferences}`)
    const native = createCanvas(160, 172)
    const context = native.getContext('2d')
    const gripSource = { x: 68, y: 75 }
    const grip = { x: 104, y: 85 }
    const radians = angle => angle * Math.PI / 180
    const rotate = (horizontal, vertical, angle) => ({
      x: horizontal * Math.cos(radians(angle)) - vertical * Math.sin(radians(angle)),
      y: horizontal * Math.sin(radians(angle)) + vertical * Math.cos(radians(angle)),
    })
    const transformPoint = (horizontal, vertical, part, lean) => {
      const relative = rotate(horizontal - part.x, vertical - part.y, part.angle)
      const global = rotate(relative.x + part.x - gripSource.x, relative.y + part.y - part.lift - gripSource.y, lean)
      return { x: grip.x + global.x, y: grip.y + global.y }
    }
    const polygon = (points, color) => {
      context.fillStyle = color
      const left = Math.floor(Math.min(...points.map(point => point.x)))
      const right = Math.ceil(Math.max(...points.map(point => point.x)))
      const top = Math.floor(Math.min(...points.map(point => point.y)))
      const bottom = Math.ceil(Math.max(...points.map(point => point.y)))
      for (let vertical = top; vertical <= bottom; vertical++) {
        for (let horizontal = left; horizontal <= right; horizontal++) {
          let inside = false
          let previous = points.length - 1
          for (let index = 0; index < points.length; previous = index++) {
            const currentPoint = points[index]
            const previousPoint = points[previous]
            if ((currentPoint.y > vertical + 0.5) !== (previousPoint.y > vertical + 0.5) && horizontal + 0.5 < (previousPoint.x - currentPoint.x) * (vertical + 0.5 - currentPoint.y) / (previousPoint.y - currentPoint.y) + currentPoint.x) inside = !inside
          }
          if (inside) context.fillRect(horizontal, vertical, 1, 1)
        }
      }
    }
    let rigidDraws = 0
    const paint = (name, part, lean) => {
      const pivot = transformPoint(part.x, part.y, part, lean)
      context.save()
      context.translate(pivot.x, pivot.y)
      context.rotate(radians(lean + part.angle))
      const matrix = context.getTransform()
      if (Math.abs(Math.hypot(matrix.a, matrix.b) - 1) > 1e-9 || Math.abs(Math.hypot(matrix.c, matrix.d) - 1) > 1e-9) throw new Error('Unexpected limb scaling')
      context.drawImage(parts[name], -part.x, -part.y)
      context.restore()
      rigidDraws++
    }
    const body = { x: 48, y: 85, angle: 0, lift: 0 }
    const joint = (bodyRectangle, partRectangle, part, lean, color) => {
      const points = [
        transformPoint(bodyRectangle[0], bodyRectangle[1], body, lean),
        transformPoint(bodyRectangle[2], bodyRectangle[1], body, lean),
        transformPoint(partRectangle[2], partRectangle[3], part, lean),
        transformPoint(partRectangle[0], partRectangle[3], part, lean),
      ]
      polygon(points, color)
    }
    const poseAt = frame => {
      const phase = frame / 48 * Math.PI * 2
      const drive = phase * 4
      const energy = 0.42 + 0.58 * Math.sin(phase) ** 2
      const leftReach = (1 + Math.sin(drive)) / 2
      const rightReach = (1 + Math.sin(drive + Math.PI * 0.6)) / 2
      const leftTuck = (1 + Math.sin(drive + Math.PI / 5)) / 2
      const rightTuck = 1 - leftTuck
      return {
        lean: Math.round(-8 + 2 * Math.sin(phase * 2 - 0.4)),
        head: { x: 48, y: 73, angle: 0, lift: 0 },
        leftArm: { x: 30, y: 85, angle: Math.round(10 + energy * 84 * leftReach), lift: 0 },
        rightArm: { x: 66, y: 85, angle: Math.round(-10 - energy * 84 * rightReach), lift: 0 },
        leftLeg: { x: 39, y: 99, angle: Math.round(3 + energy * 24 * leftTuck), lift: Math.round(energy * 3 * leftTuck) },
        rightLeg: { x: 58, y: 99, angle: Math.round(-3 - energy * 24 * rightTuck), lift: Math.round(energy * 3 * rightTuck) },
      }
    }
    const connectivity = () => {
      const pixels = context.getImageData(0, 0, 160, 172).data
      const visited = new Uint8Array(160 * 172)
      const components = []
      let partialAlpha = 0
      for (let offset = 0; offset < visited.length; offset++) {
        const alpha = pixels[offset * 4 + 3]
        if (alpha && alpha !== 255) partialAlpha++
        if (!alpha || visited[offset]) continue
        const queue = [offset]
        visited[offset] = 1
        for (let cursor = 0; cursor < queue.length; cursor++) {
          const current = queue[cursor]
          const horizontal = current % 160
          const vertical = Math.floor(current / 160)
          for (let deltaY = -1; deltaY <= 1; deltaY++) {
            for (let deltaX = -1; deltaX <= 1; deltaX++) {
              const nextX = horizontal + deltaX
              const nextY = vertical + deltaY
              const next = nextY * 160 + nextX
              if (nextX < 0 || nextX >= 160 || nextY < 0 || nextY >= 172 || visited[next] || !pixels[next * 4 + 3]) continue
              visited[next] = 1
              queue.push(next)
            }
          }
        }
        components.push(queue.length)
      }
      components.sort((first, second) => second - first)
      return { components, partialAlpha }
    }
    const renderCharacter = frame => {
      const pose = poseAt(frame)
      const lean = pose.lean
      context.clearRect(0, 0, 160, 172)
      joint([31, 96, 47, 101], [31, 96, 47, 102], pose.leftLeg, lean, colorAt(39, 100))
      joint([49, 96, 65, 101], [49, 96, 65, 102], pose.rightLeg, lean, colorAt(57, 100))
      paint('leftLeg', pose.leftLeg, lean)
      paint('rightLeg', pose.rightLeg, lean)
      const legPixels = context.getImageData(0, 0, 160, 172)
      for (let vertical = 0; vertical < 172; vertical++) {
        for (let horizontal = 0; horizontal < 160; horizontal++) {
          const offset = (vertical * 160 + horizontal) * 4
          if (!legPixels.data[offset + 3]) continue
          const position = rotate(horizontal + 0.5 - grip.x, vertical + 0.5 - grip.y, -lean)
          if (position.y + gripSource.y < 99) legPixels.data.fill(0, offset, offset + 4)
        }
      }
      context.putImageData(legPixels, 0, 0)
      paint('body', body, lean)
      paint('hips', body, lean)
      joint([31, 80, 38, 86], [25, 81, 35, 88], pose.leftArm, lean, colorAt(30, 85))
      joint([59, 80, 66, 86], [61, 81, 71, 88], pose.rightArm, lean, colorAt(65, 85))
      paint('leftArm', pose.leftArm, lean)
      paint('rightArm', pose.rightArm, lean)
      polygon([
        transformPoint(29, 79, body, lean),
        transformPoint(68, 79, body, lean),
        transformPoint(68, 70, pose.head, lean),
        transformPoint(29, 70, pose.head, lean),
      ], colorAt(36, 76))
      paint('head', pose.head, lean)
      return { pose, ...connectivity() }
    }
    const effects = createCanvas(160, 172)
    const effectContext = effects.getContext('2d')
    const renderEffects = frame => {
      effectContext.clearRect(0, 0, 160, 172)
      const current = poseAt(frame)
      const previous = poseAt(frame - 1)
      let strokes = 0
      const flutter = (part, hand, sign) => {
        if (Math.abs(current[part].angle - previous[part].angle) < 12) return
        const position = transformPoint(hand.x, hand.y, current[part], current.lean)
        const horizontal = Math.round(position.x + sign * 7)
        const vertical = Math.round(position.y - 4)
        effectContext.fillStyle = '#8293ad'
        effectContext.fillRect(horizontal, vertical, 1, 3)
        effectContext.fillRect(horizontal + sign, vertical + 3, 1, 2)
        effectContext.fillRect(horizontal + sign * 2, vertical + 5, 1, 1)
        effectContext.fillStyle = '#b5c1d2'
        effectContext.fillRect(horizontal + sign * 4, vertical + 1, 1, 2)
        effectContext.fillRect(horizontal + sign * 5, vertical + 3, 1, 2)
        effectContext.fillRect(horizontal + sign * 6, vertical + 5, 1, 1)
        strokes += 2
      }
      flutter('leftArm', { x: 28, y: 96 }, -1)
      flutter('rightArm', { x: 67, y: 96 }, 1)
      const activeFoot = current.leftLeg.lift < previous.leftLeg.lift ? 'leftLeg' : current.rightLeg.lift < previous.rightLeg.lift ? 'rightLeg' : null
      if (activeFoot && frame % 2 === 0) {
        const position = transformPoint(activeFoot === 'leftLeg' ? 39 : 58, 110, current[activeFoot], current.lean)
        effectContext.fillStyle = '#a3b1c5'
        effectContext.fillRect(Math.round(position.x - 1), Math.round(position.y + 5), 5, 1)
        effectContext.fillStyle = '#c1cad7'
        effectContext.fillRect(Math.round(position.x), Math.round(position.y + 8), 3, 1)
        strokes += 2
      }
      return strokes
    }
    const bodyAtlas = createCanvas(160 * 8, 172 * 6)
    const effectAtlas = createCanvas(160 * 8, 172 * 6)
    const frames = []
    for (let frame = 0; frame < 48; frame++) {
      const result = renderCharacter(frame)
      if (result.components.length !== 1 || result.partialAlpha) throw new Error(`Invalid pickup frame ${frame}`)
      renderEffects(frame)
      const horizontal = frame % 8 * 160
      const vertical = Math.floor(frame / 8) * 172
      bodyAtlas.getContext('2d').drawImage(native, horizontal, vertical)
      effectAtlas.getContext('2d').drawImage(effects, horizontal, vertical)
      const pixels = context.getImageData(0, 0, 160, 172).data
      let contentTop = 172
      for (let offset = 0; offset < pixels.length; offset += 4) {
        if (pixels[offset + 3]) contentTop = Math.min(contentTop, Math.floor(offset / 4 / 160))
      }
      frames.push({ source: [horizontal, vertical, 160, 172], contentTop })
    }
    return {
      body: bodyAtlas.toDataURL(), effects: effectAtlas.toDataURL(),
      metadata: {
        frameWidth: 160, frameHeight: 172, frameCount: 48, frameRate: 20,
        scale: 0.5, anchorX: 84, anchorY: 122,
        neutralTop: 10 + Math.floor(originalPixels.findIndex((value, index) => index % 4 === 3 && value > 0) / 4 / 96),
        sourceSha256: '708319759ba0105a1896b88f243228b08778b3980cbedab67a5afda88b785e6a',
        frames,
      },
      neutralDifferences,
    }
  })
  const directory = 'public/assets/character/pickup/ethan'
  await page.evaluate(({ body, effects }) => {
    window.__pickupExport = { body, effects }
  }, setup)
  for (const layer of ['body', 'effects']) {
    const download = page.waitForEvent('download')
    await page.evaluate(layer => {
      const link = document.createElement('a')
      link.href = window.__pickupExport[layer]
      link.download = `${layer}.png`
      link.click()
    }, layer)
    await (await download).saveAs(`${directory}/${layer}.png`)
  }
  const download = page.waitForEvent('download')
  await page.evaluate(metadata => {
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([JSON.stringify(metadata, null, 2) + '\n'], { type: 'application/json' }))
    link.download = 'frames.json'
    link.click()
  }, setup.metadata)
  await (await download).saveAs(`${directory}/frames.json`)
  return { neutralDifferences: setup.neutralDifferences, frames: setup.metadata.frameCount, frameRate: setup.metadata.frameRate }
}
