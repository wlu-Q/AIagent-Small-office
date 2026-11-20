async page => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.addInitScript(() => {
    const template = window.__workstationEmployees[0]
    window.__workstationEmployees = [
      { ...template, id: 'upper-employee', displayName: 'Upper', workstationId: 'workstation-13' },
      { ...template, id: 'lower-employee', displayName: 'Lower', workstationId: 'workstation-01' },
    ]
  })
  await page.goto(`http://127.0.0.1:5179/?workstation-isolation=${Date.now()}#/office`)
  await page.bringToFront()
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.officeEmployees?.size === 2)
  const approach = async id => {
    await page.evaluate(id => {
      const scene = window.game.scene.getScene('game')
      const workstation = scene.workstationComputers.get(id).workstation
      const offset = workstation.direction === 'up' ? -32 : 32
      scene.playerSelector.selectedItem?.clearDialogBox()
      scene.playerSelector.selectedItem = undefined
      scene.myPlayer.body.reset(workstation.x + 56, workstation.y + offset)
      scene.myPlayer.playerContainer.body.reset(workstation.x + 56, workstation.y + offset - 30)
      scene.playerSelector.setPosition(workstation.x + 24, workstation.y + offset)
    }, id)
    await page.waitForFunction(id => window.game.scene.getScene('game').playerSelector.selectedItem?.workstation?.id === id, id)
  }
  const updateUpper = async state => {
    await page.evaluate(async state => {
      if (state === 'unbound') window.__workstationEmployees = window.__workstationEmployees.filter(employee => employee.id !== 'upper-employee')
      else window.__workstationEmployees.find(employee => employee.id === 'upper-employee').state = state
      const { officeApi } = await import('/src/services/office/officeApi.ts')
      officeApi.invalidateEmployees()
      await officeApi.listEmployees({ fresh: true })
    }, state)
    await page.locator(`[data-workstation-state="${state}"]`).waitFor()
    const lower = await page.evaluate(() => window.game.scene.getScene('game').officeEmployees.get('lower-employee').isOperatingWorkstation())
    if (!lower) throw new Error('Upper state change affected the lower employee')
  }
  await approach('workstation-13')
  await page.evaluate(() => {
    const input = document.createElement('input')
    input.id = 'workstation-input-test'
    document.body.append(input)
    input.focus()
  })
  await page.keyboard.press('r', { delay: 100 })
  if (await page.getByRole('dialog').isVisible()) throw new Error('R opened while typing')
  await page.evaluate(() => document.querySelector('#workstation-input-test').remove())
  await page.keyboard.press('r', { delay: 100 })
  await page.locator('[data-operating="true"][data-artwork-ready="true"]').waitFor()
  if (!(await page.locator('#workstation-preview-title').innerText()).startsWith('Upper')) throw new Error('Upper desk opened lower binding')
  await updateUpper('disabled')
  if (await page.locator('[data-screen-clip]').count()) throw new Error('Disabled upper desk is still animated')
  await updateUpper('idle')
  if (await page.locator('[data-screen-clip]').count()) throw new Error('Idle upper desk is still animated')
  await page.keyboard.press('Escape')
  await page.locator('.workstation-preview').waitFor({ state: 'detached' })
  const interactionStates = await page.evaluate(() => {
    const employees = window.game.scene.getScene('game').officeEmployees
    return { upper: employees.get('upper-employee').reaction.hitArea.input.enabled, lower: employees.get('lower-employee').reaction.hitArea.input.enabled }
  })
  if (!interactionStates.upper || interactionStates.lower) throw new Error('Idle and working interaction rules differ by row')
  await page.keyboard.press('r', { delay: 100 })
  await page.locator('[data-workstation-state="idle"]').waitFor()
  await updateUpper('unbound')
  await page.locator('[data-artwork-ready="true"]').waitFor()
  if (await page.locator('[data-screen-clip]').count()) throw new Error('Unbound upper desk is animated')
  if (await page.locator('[data-hand-style]').count()) throw new Error('Unbound upper desk retained hands')
  await page.waitForTimeout(250)
  await page.screenshot({ path: 'output/playwright/workstation-upper-unbound.png' })
  await page.keyboard.press('Escape')
  await page.locator('.workstation-preview').waitFor({ state: 'detached' })
  await approach('workstation-01')
  await page.keyboard.press('r', { delay: 100 })
  await page.locator('[data-operating="true"][data-artwork-ready="true"]').waitFor()
  if (!(await page.locator('#workstation-preview-title').innerText()).startsWith('Lower')) throw new Error('Lower desk binding was lost')
  await page.keyboard.press('Escape')
  await page.locator('.workstation-preview').waitFor({ state: 'detached' })
  await page.evaluate(() => {
    const scene = window.game.scene.getScene('game')
    scene.myPlayer.body.reset(-48, 195)
    scene.myPlayer.playerContainer.body.reset(-48, 165)
  })
  await page.keyboard.press('r', { delay: 100 })
  if (await page.getByRole('dialog').isVisible()) throw new Error('Stale lower selector opened from the upper side')
  if (errors.length) throw new Error(errors.join('\n'))
  return { independentBindings: true, independentStates: ['working', 'disabled', 'idle', 'unbound'], typingProtected: true, wrongSideRejected: true, interactionStates, errors }
}
