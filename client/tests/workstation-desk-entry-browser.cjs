async page => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.goto(`http://127.0.0.1:5179/?desk-entry=${Date.now()}#/office`)
  await page.bringToFront()
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.workstationComputers?.size === 24)
  const workstations = await page.evaluate(() => [...window.game.scene.getScene('game').workstationComputers.values()].map(computer => computer.workstation))
  const verified = []
  for (const workstation of workstations) {
    for (const side of [-1, 1]) {
      await page.evaluate(({ workstation, side }) => {
        const scene = window.game.scene.getScene('game')
        const desktopY = workstation.y + (workstation.direction === 'up' ? -32 : 32)
        scene.playerSelector.selectedItem?.clearDialogBox()
        scene.playerSelector.selectedItem = undefined
        scene.myPlayer.body.reset(workstation.x + 70 * side, desktopY)
        scene.myPlayer.playerContainer.body.reset(workstation.x + 70 * side, desktopY - 30)
        scene.playerSelector.setPosition(workstation.x + 70 * side, desktopY)
      }, { workstation, side })
      const approachKey = side === 1 ? 'a' : 'd'
      await page.keyboard.down(approachKey)
      await page.waitForFunction(id => {
        const scene = window.game.scene.getScene('game')
        return scene.playerSelector.selectedItem?.workstation?.id === id && scene.playerSelector.selectedItem.dialogKey === 'ui.press_r_to_view_workstation'
      }, workstation.id, { timeout: 3000 })
      await page.keyboard.up(approachKey)
      await page.keyboard.press('e', { delay: 100 })
      if (await page.evaluate(() => window.game.scene.getScene('game').myPlayer.playerBehavior !== 0)) throw new Error('Desktop handled E as a chair')
      for (let attempt = 0; attempt < 2; attempt++) {
        await page.keyboard.press('r', { delay: 150 })
        await page.getByRole('dialog').waitFor({ state: 'visible', timeout: 3000 })
        if (await page.locator('.workstation-preview__stage').getAttribute('data-workstation-id') !== workstation.id) throw new Error(`Wrong desk: ${workstation.id}`)
        await page.keyboard.press('Escape')
        await page.locator('.workstation-preview').waitFor({ state: 'detached' })
      }
      verified.push(`${workstation.id}:${side}`)
    }
  }
  if (errors.length) throw new Error(errors.join('\n'))
  return { verifiedApproaches: verified.length, repeatedOpenings: verified.length * 2, errors }
}
