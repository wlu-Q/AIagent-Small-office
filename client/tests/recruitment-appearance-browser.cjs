async page => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.addInitScript(() => {
    document.addEventListener('readystatechange', () => {
      const legacy = { ...window.__workstationEmployees[0], avatar: 'adam' }
      const saved = JSON.parse(sessionStorage.getItem('hired-appearance') || '[]')
      window.__workstationEmployees = [legacy, ...saved]
      window.officeDesktopBridge.listOfficeEmployeeTemplates = async () => ({
        templates: [{ templateId: 'product-manager', role: '产品经理', category: '', tags: [], specialties: [] }],
      })
      window.officeDesktopBridge.createOfficeEmployee = async payload => {
        const employee = { ...legacy, ...payload, state: 'idle', workstationId: saved.length ? 'workstation-06' : 'workstation-05' }
        saved.push(employee)
        sessionStorage.setItem('hired-appearance', JSON.stringify(saved))
        window.__workstationEmployees.push(employee)
        return { employee }
      }
    }, { once: true })
  })
  await page.goto('http://127.0.0.1:5179/#/office')
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.officeEmployees?.get('workstation-preview-employee')?.playerTexture === 'victor')
  await page.locator('[data-office-employee-manager-launcher]').click()
  const hire = page.locator('[data-create-employee-open]')
  await hire.click()
  const picker = page.getByRole('combobox', { name: '员工形象' })
  const options = await picker.locator('option').evaluateAll(items => items.map(item => item.value))
  const expected = ['ethan', 'noah', 'victor', 'leo', 'olivia', 'chloe', 'maya', 'luna', 'emma']
  if (options.length !== expected.length || !expected.every(name => options.includes(name))) throw new Error('Unexpected employee appearances')
  for (const texture of options) {
    await picker.selectOption(texture)
    const matches = await page.getByRole('img', { name: texture, exact: true }).evaluate(async (sprite, texture) => {
      const scene = window.game.scene.getScene('game')
      const frame = scene.anims.get(texture + '_idle_down').frames[0].frame
      const style = getComputedStyle(sprite)
      const image = new Image()
      image.src = style.backgroundImage.slice(5, -2)
      await image.decode()
      const canvas = document.createElement('canvas')
      canvas.width = sprite.clientWidth
      canvas.height = sprite.clientHeight
      if (canvas.width !== frame.cutWidth || canvas.height !== frame.cutHeight) return false
      const context = canvas.getContext('2d', { willReadFrequently: true })
      const pixels = (sheet, left, top) => {
        context.clearRect(0, 0, canvas.width, canvas.height)
        context.drawImage(sheet, left, top)
        return context.getImageData(0, 0, canvas.width, canvas.height).data
      }
      const preview = pixels(image, parseFloat(style.backgroundPositionX), parseFloat(style.backgroundPositionY))
      const actor = pixels(frame.source.image, -frame.cutX, -frame.cutY)
      return preview.some(channel => channel > 0) && preview.every((channel, index) => channel === actor[index])
    }, texture)
    if (!matches) throw new Error(texture + ' preview differs from its standing frame')
  }
  await page.getByRole('button', { name: '取消', exact: true }).click()
  if (await page.evaluate(() => sessionStorage.getItem('hired-appearance'))) throw new Error('Cancel created an employee')
  const verifyHire = texture => page.waitForFunction(texture => {
    const hired = JSON.parse(sessionStorage.getItem('hired-appearance') || '[]').find(employee => employee.avatar === texture)
    const actor = window.game?.scene.getScene('game')?.officeEmployees?.get(hired?.id)
    return actor?.playerTexture === texture && actor.reaction.character === texture && !!actor.reaction.pickup
  }, texture)
  const hires = ['chloe', 'emma']
  for (const texture of hires) {
    await hire.click()
    await picker.selectOption(texture)
    await page.locator('[data-create-employee-name]').fill(texture)
    await page.locator('[data-create-employee]').click()
    await verifyHire(texture)
  }
  await page.reload()
  for (const texture of hires) await verifyHire(texture)
  await page.evaluate(textures => {
    const scene = window.game.scene.getScene('game')
    for (const texture of textures) {
      const keys = ['right', 'up', 'left', 'down'].flatMap(direction =>
        ['idle', 'run', 'sit'].map(action => texture + '_' + action + '_' + direction))
      keys.push(...['nod', 'scratch', 'clap', 'stretch', 'jump'].map(action => texture + '_reaction_' + action), texture + '-pickup')
      for (const key of keys) {
        const animation = scene.anims.get(key)
        if (!animation?.frames.length || !animation.frames.every(item => item.frame && item.frame.texture.key !== '__MISSING')) {
          throw new Error('Missing animation frames: ' + key)
        }
      }
    }
  }, options)
  return { previews: options, hired: hires, legacy: 'victor' }
}
