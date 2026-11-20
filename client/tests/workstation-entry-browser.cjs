async page => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.goto(`http://127.0.0.1:5179/?entry-test=${Date.now()}#/office`)
  await page.bringToFront()
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.myPlayer)
  await page.evaluate(() => {
    const scene = window.game.scene.getScene('game')
    scene.myPlayer.setPosition(-48, 573).play(`${scene.myPlayer.playerTexture}_idle_up`, true)
    scene.myPlayer.playerContainer.setPosition(-48, 543)
    scene.playerSelector.setPosition(-48, 541)
    scene.cameras.main.stopFollow().setZoom(1.5).centerOn(-48, 530)
  })
  await page.waitForFunction(() => window.game.scene.getScene('game').playerSelector.selectedItem?.itemType === 0)
  const chairHint = await page.evaluate(() => window.game.scene.getScene('game').playerSelector.selectedItem.dialogKey)
  if (chairHint !== 'ui.press_e_to_sit') throw new Error('Chair must only offer E')
  await page.keyboard.press('r', { delay: 250 })
  if (await page.getByRole('dialog').count()) throw new Error('Chair must not open a workstation with R')
  await page.keyboard.press('e', { delay: 250 })
  await page.waitForFunction(() => window.game.scene.getScene('game').playerSelector.selectedItem?.itemType === 1)
  const entry = await page.evaluate(() => {
    const scene = window.game.scene.getScene('game')
    return {
      hint: scene.playerSelector.selectedItem.dialogKey,
      extraHint: scene.children.list.some(child => child.text === 'R · 04'),
      sitting: scene.myPlayer.playerBehavior === 1,
    }
  })
  if (entry.hint !== 'ui.press_r_to_view_workstation' || entry.extraHint || !entry.sitting) throw new Error(JSON.stringify(entry))
  await page.screenshot({ path: 'output/playwright/workstation-existing-r-entry.png' })
  await page.keyboard.press('r', { delay: 250 })
  await page.getByRole('dialog').waitFor({ state: 'visible' })
  await page.locator('[data-artwork-ready="true"]').waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.workstation-preview').waitFor({ state: 'detached' })
  await page.keyboard.press('e', { delay: 250 })
  await page.waitForFunction(() => window.game.scene.getScene('game').myPlayer.playerBehavior === 0)
  return { ...entry, chairOnlyE: true, openedViaExistingR: true, stoodUpAfterClosing: true }
}
