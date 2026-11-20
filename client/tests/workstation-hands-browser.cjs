async page => {
  const errors = []
  const requests = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (request.url().includes('/assets/workstations/hands')) requests.push(request.url()) })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.goto(`http://127.0.0.1:5179/?workstation-hands=${Date.now()}#/office`)
  await page.bringToFront()
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.officeEmployees?.size === 1)
  await page.evaluate(() => {
    const scene = window.game.scene.getScene('game')
    scene.myPlayer.setPosition(-48, 573).play(`${scene.myPlayer.playerTexture}_idle_up`, true)
    scene.myPlayer.playerContainer.setPosition(-48, 543)
    scene.playerSelector.setPosition(-48, 541)
  })
  await page.waitForFunction(() => window.game.scene.getScene('game').playerSelector.selectedItem?.itemType === 0)
  await page.keyboard.press('e', { delay: 200 })
  await page.waitForFunction(() => window.game.scene.getScene('game').playerSelector.selectedItem?.itemType === 1)
  await page.keyboard.press('r', { delay: 200 })
  await page.locator('[data-operating="true"][data-artwork-ready="true"]').waitFor()
  if (new Set(requests).size !== 1) throw new Error('Opening one workstation eagerly loaded other arm sets')
  const variants = { ethan: 'navy', noah: 'shirt', victor: 'hoodie', leo: 'hoodie', olivia: 'blouse', chloe: 'cardigan', maya: 'suit', luna: 'cardigan', emma: 'blouse' }
  for (const [texture, style] of Object.entries(variants)) {
    await page.evaluate(texture => {
      const employee = [...window.game.scene.getScene('game').officeEmployees.values()][0]
      employee.playerTexture = texture
      employee.anims.play(`${texture}_sit_up`, true)
    }, texture)
    await page.locator(`[data-hand-style="${style}"][data-artwork-ready="true"][data-operating="true"]`).waitFor()
    await page.waitForTimeout(200)
    await page.locator('.workstation-preview').screenshot({ path: `output/playwright/workstation-hands-${texture}.png` })
  }
  if (new Set(requests).size !== 6) throw new Error('Expected exactly six loaded arm sheets')
  const styleBefore = await page.locator('[data-hand-style]').getAttribute('data-hand-style')
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await page.locator('.workstation-preview').waitFor({ state: 'detached' })
  await page.keyboard.press('r', { delay: 200 })
  await page.locator(`[data-hand-style="${styleBefore}"][data-artwork-ready="true"]`).waitFor()
  if (new Set(requests).size !== 6) throw new Error('Reopening randomized the hand style')
  const frames = await page.evaluate(async () => {
    const { drawWorkstation } = await import('/src/workstations/drawWorkstation.ts')
    const { loadWorkstationArtwork } = await import('/src/workstations/workstationArtwork.ts')
    const { createDeskLayout, HAND_CYCLE_MS } = await import('/src/workstations/workstationModel.ts')
    const source = document.createElement('canvas')
    source.width = 1672; source.height = 941
    const context = source.getContext('2d')
    const preview = document.createElement('canvas')
    preview.width = 836; preview.height = 471
    const scaled = preview.getContext('2d')
    scaled.imageSmoothingEnabled = false
    const artwork = await loadWorkstationArtwork('blouse')
    const layout = createDeskLayout('workstation-04', 3)
    const images = []
    for (let time = 0; time < HAND_CYCLE_MS; time += 50) {
      drawWorkstation(context, artwork, layout, true, true, time)
      scaled.drawImage(source, 0, 0, preview.width, preview.height)
      images.push(preview.toDataURL('image/png').split(',')[1])
    }
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([JSON.stringify({ delay: 50, images })], { type: 'application/json' }))
    link.download = 'workstation-motion-frames.json'
    document.body.appendChild(link)
    window.__workstationPreviewDownload = link
    return images.length
  })
  const downloadPromise = page.waitForEvent('download')
  await page.evaluate(() => window.__workstationPreviewDownload.click())
  const download = await downloadPromise
  await download.saveAs('output/playwright/workstation-motion-frames.json')
  await page.evaluate(() => {
    URL.revokeObjectURL(window.__workstationPreviewDownload.href)
    window.__workstationPreviewDownload.remove()
    delete window.__workstationPreviewDownload
  })
  if (errors.length) throw new Error(errors.join('\n'))
  return { appearances: Object.keys(variants).length, sheetsLoaded: new Set(requests).size, retainedStyle: styleBefore, previewFrames: frames, errors }
}
