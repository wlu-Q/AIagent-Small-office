const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')

exports.run = async function run(window, waitForRenderer) {
  const evaluate = source => window.webContents.executeJavaScript(source)
  window.showInactive()
  await waitForRenderer(window, `window.game?.scene.isActive('background') && window.game.scene.keys.background.cloud?.getLength() === 24`, 'background assets loaded')
  await evaluate(`(() => {
    window.backgroundTest = { RealDate: Date, gameScene: window.game.scene.keys.game,
      previousBackdrop: window.game.scene.keys.background.backdropImage }
    window.backgroundTest.now = new Date(2026, 10, 5, 0, 0, 0).getTime()
    window.Date = class extends window.backgroundTest.RealDate {
      constructor(...args) { super(...(args.length ? args : [window.backgroundTest.now])) }
      static now() { return window.backgroundTest.now }
    }
    window.game.scene.keys.background.scene.restart()
  })()`)
  await waitForRenderer(window, `window.game.scene.keys.background.backdropImage !== window.backgroundTest.previousBackdrop`, 'initial scene restart finished')

  const expectMode = async mode => {
    await waitForRenderer(window, `(() => {
      const scene = window.game.scene.keys.background
      return scene.backdropImage.texture.key === 'backdrop_${mode}' &&
        scene.cloud.getChildren().every(cloud => cloud.texture.key === 'cloud_${mode}')
    })()`, `all background layers use ${mode}`)
  }
  const checkCoverage = async () => {
    await waitForRenderer(window, `(() => {
      const scene = window.game.scene.keys.background
      const camera = scene.cameras.main
      const canvas = window.game.canvas
      return Math.abs(camera.width - canvas.width) < 2 && Math.abs(camera.height - canvas.height) < 2 &&
        [scene.backdropImage, scene.sunMoonImage].every(image => {
          const rect = image.getBounds()
          return rect.left <= 0.01 && rect.top <= 0.01 &&
            rect.right >= camera.width - 0.01 && rect.bottom >= camera.height - 0.01
        }) && scene.physics.world.bounds.width === camera.width &&
        scene.physics.world.bounds.height === camera.height
    })()`, 'sky and celestial layer cover resized viewport; cloud bounds follow').catch(async error => {
      console.error(JSON.stringify(await evaluate(`(() => {
        const scene = window.game.scene.keys.background
        return { viewport: [innerWidth, innerHeight], gameSize: [window.game.scale.width, window.game.scale.height],
          camera: [scene.cameras.main.width, scene.cameras.main.height], container: document.querySelector('#phaser-container').getBoundingClientRect().toJSON(),
          images: [scene.backdropImage, scene.sunMoonImage].map(image => ({ texture: image.texture.key, bounds: image.getBounds() })),
          world: scene.physics.world.bounds }
      })()`), null, 2))
      throw error
    })
    assert.equal(await evaluate(`window.game.scene.keys.background.cloud.getLength()`), 24)
    assert.equal(await evaluate(`window.game.scene.keys.game === window.backgroundTest.gameScene`), true, 'office scene is not restarted')
  }
  const setTime = async (hour, minute, second, mode) => {
    await evaluate(`window.backgroundTest.now = new window.backgroundTest.RealDate(2026, 10, 5, ${hour}, ${minute}, ${second}).getTime()`)
    await expectMode(mode)
    await checkCoverage()
  }
  const evidenceDir = path.resolve(__dirname, '../output/playwright')
  await fs.mkdir(evidenceDir, { recursive: true })
  try {
    await expectMode('night') // Initial scene creation must honor local time.
    await checkCoverage()
    await evaluate(`void (window.backgroundTest.clouds = [...window.game.scene.keys.background.cloud.getChildren()])`)
    await setTime(5, 59, 59, 'night')
    await setTime(6, 0, 0, 'day')
    await setTime(17, 59, 59, 'day')
    for (const [width, height] of [[960, 640], [1600, 1000], [900, 1100]]) {
      window.setContentSize(width, height)
      for (const zoom of [0.7, 1, 1.8, 1]) {
        await evaluate(`window.officeDesktopBridge.setZoomFactor(${zoom})`)
        // Chromium resize delivery is asynchronous after the IPC reply.
        await new Promise(resolve => setTimeout(resolve, 150))
        await checkCoverage()
      }
    }
    window.setContentSize(1440, 900)
    await evaluate(`window.officeDesktopBridge.setZoomFactor(0.7)`)
    await new Promise(resolve => setTimeout(resolve, 200))
    await checkCoverage()
    await fs.writeFile(path.join(evidenceDir, 'background-day-zoom.png'), (await window.webContents.capturePage()).toPNG())
    await setTime(18, 0, 0, 'night')
    await new Promise(resolve => setTimeout(resolve, 150))
    await fs.writeFile(path.join(evidenceDir, 'background-night-zoom.png'), (await window.webContents.capturePage()).toPNG())
    await setTime(23, 59, 59, 'night')
    await setTime(0, 0, 0, 'night')
    await setTime(8, 0, 0, 'day') // Resume after a long gap.
    await setTime(4, 0, 0, 'night') // System clock moved backwards.
    assert.equal(await evaluate(`window.backgroundTest.clouds.every((cloud, index) => cloud === window.game.scene.keys.background.cloud.getChildren()[index])`), true, 'day/night preserves cloud instances')

    const listeners = await evaluate(`window.game.scale.listenerCount('resize')`)
    for (let count = 0; count < 3; count++) {
      await evaluate(`(() => {
        window.backgroundTest.previousBackdrop = window.game.scene.keys.background.backdropImage
        window.game.scene.keys.background.scene.restart()
      })()`)
      await waitForRenderer(window, `window.game.scene.keys.background.backdropImage !== window.backgroundTest.previousBackdrop`, 'scene restart finished')
      await expectMode('night')
      await checkCoverage()
      assert.equal(await evaluate(`window.game.scale.listenerCount('resize')`), listeners, 'scene restart does not leak resize listeners')
    }
    console.log('DESKTOP_BACKGROUND_SMOKE_OK: zoom 0.7/1/1.8, landscape/portrait resize, local day/night boundaries, clock jumps, restart cleanup')
  } finally {
    await evaluate(`window.Date = window.backgroundTest.RealDate; delete window.backgroundTest`)
  }
}
