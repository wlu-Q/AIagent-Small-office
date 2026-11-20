async page => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.addInitScript(() => {
    const template = window.__workstationEmployees[0]
    window.__workstationEmployees = Array.from({ length: 24 }, (_, index) => ({
      ...template, id: `seat-employee-${index + 1}`, displayName: `Employee ${index + 1}`,
      workstationId: `workstation-${String(index + 1).padStart(2, '0')}`,
    }))
  })
  await page.goto(`http://127.0.0.1:5179/?workstation-capacity=${Date.now()}#/office`)
  await page.bringToFront()
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.officeEmployees?.size === 24)
  const audit = await page.evaluate(() => {
    const scene = window.game.scene.getScene('game')
    if (scene.workstationComputers.size !== 24) throw new Error('Missing independent computer targets')
    const chairs = scene.children.list.filter(item => item.itemType === 0 && item.computer)
    if (chairs.length !== 24 || new Set(chairs.map(chair => chair.computer)).size !== 24) throw new Error('Chairs share a workstation target')
    return [...scene.officeEmployees.values()].map(employee => {
      const workstation = employee.workstation
      const agent = employee.getNavigationAgent()
      const position = { x: workstation.x, y: workstation.y + (workstation.direction === 'up' ? 64 : -40) }
      const route = scene.officeNavigation.findRoute(position, workstation, agent)
      if (!scene.officeNavigation.isWorldPositionWalkable(workstation, agent)) throw new Error(`Blocked seat: ${workstation.id}`)
      if (!route.waypoints.length) throw new Error(`Unreachable seat: ${workstation.id}`)
      if (!employee.isOperatingWorkstation() || !employee.anims.currentAnim.key.endsWith(`_sit_${workstation.direction}`)) throw new Error(`Wrong working pose: ${workstation.id}`)
      if (Number(workstation.id.slice(-2)) <= 12) {
        const left = Number(workstation.id.slice(-2)) <= 6
        if (employee.y !== workstation.y + (left ? 3 : 0) || employee.depth !== workstation.y + (left ? -10 : 0)) throw new Error(`Original lower seat changed: ${workstation.id}`)
      }
      employee.body.reset(position.x, position.y)
      employee.playerContainer.setPosition(position.x, position.y - 30)
      employee.applyProjection(employee.getProjection())
      return { ...workstation }
    })
  })
  await page.waitForFunction(() => [...window.game.scene.getScene('game').officeEmployees.values()].every(employee => employee.isOperatingWorkstation() && Math.hypot(employee.x - employee.workstation.x, employee.y - employee.workstation.y) <= 8), null, { timeout: 15000 })
  await page.evaluate(() => window.game.scene.getScene('game').cameras.main.stopFollow().setZoom(0.8).centerOn(490, 450))
  await page.screenshot({ path: 'output/playwright/workstation-24-working.png' })
  const verified = []
  for (const workstation of audit) {
    await page.evaluate(workstation => {
      const scene = window.game.scene.getScene('game')
      const offset = workstation.direction === 'up' ? 29 : -29
      scene.playerSelector.selectedItem?.clearDialogBox()
      scene.playerSelector.selectedItem = undefined
      scene.myPlayer.body.reset(workstation.x, workstation.y + offset)
      scene.myPlayer.playerContainer.body.reset(workstation.x, workstation.y + offset - 30)
      scene.myPlayer.play(`${scene.myPlayer.playerTexture}_idle_${workstation.direction}`, true)
      scene.playerSelector.setPosition(workstation.x, workstation.y + (offset > 0 ? -3 : 3))
      scene.cameras.main.stopFollow().setZoom(1.5).centerOn(workstation.x, workstation.y)
    }, workstation)
    try {
      await page.waitForFunction(id => {
        const item = window.game.scene.getScene('game').playerSelector.selectedItem
        return item?.itemType === 0 && item.computer?.workstation.id === id
      }, workstation.id, { timeout: 4000 })
      const chairHint = await page.evaluate(() => window.game.scene.getScene('game').playerSelector.selectedItem.dialogKey)
      if (chairHint !== 'ui.press_e_to_sit') throw new Error('Chair offered a combined action')
      await page.keyboard.press('r', { delay: 100 })
      if (await page.getByRole('dialog').count()) throw new Error('Chair opened a workstation with R')
      await page.keyboard.press('e', { delay: 100 })
      await page.waitForFunction(id => {
        const scene = window.game.scene.getScene('game')
        return scene.myPlayer.playerBehavior === 1 && scene.playerSelector.selectedItem?.workstation?.id === id
      }, workstation.id, { timeout: 4000 })
      const hint = await page.evaluate(() => window.game.scene.getScene('game').playerSelector.selectedItem.dialogKey)
      if (hint !== 'ui.press_r_to_view_workstation') throw new Error('Missing seated R hint')
      if (workstation.id === 'workstation-13') {
        await page.waitForTimeout(250)
        await page.screenshot({ path: 'output/playwright/workstation-upper-r-hint.png' })
      }
      await page.keyboard.press('r', { delay: 100 })
      await page.getByRole('dialog').waitFor({ state: 'visible' })
      await page.locator('[data-operating="true"][data-artwork-ready="true"]').waitFor()
      const expectedName = `Employee ${Number(workstation.id.slice(-2))}`
      if (!(await page.locator('#workstation-preview-title').innerText()).startsWith(expectedName + '的')) throw new Error('Employee binding crossed desks')
      if (await page.locator('.workstation-preview__stage').getAttribute('data-workstation-id') !== workstation.id) throw new Error('Seated R opened another desk')
      if (workstation.id === 'workstation-13') {
        await page.waitForTimeout(250)
        await page.screenshot({ path: 'output/playwright/workstation-upper-preview.png' })
      }
      await page.keyboard.press('Escape')
      await page.locator('.workstation-preview').waitFor({ state: 'detached' })
      await page.keyboard.press('e', { delay: 100 })
      await page.waitForFunction(() => window.game.scene.getScene('game').myPlayer.playerBehavior === 0)
      verified.push(workstation.id)
    } catch (error) {
      const state = await page.evaluate(() => {
        const scene = window.game.scene.getScene('game')
        const item = scene.playerSelector.selectedItem
        return { player: [scene.myPlayer.x, scene.myPlayer.y, scene.myPlayer.playerBehavior], selector: [scene.playerSelector.x, scene.playerSelector.y], selected: item && { type: item.itemType, id: item.workstation?.id || item.computer?.workstation.id } }
      })
      throw new Error(`${workstation.id}: ${error.message}; ${JSON.stringify(state)}`)
    }
  }
  if (errors.length) throw new Error(errors.join('\n'))
  return { independentWorkstations: audit.length, returnedToOwnSeat: 24, verified, errors }
}
