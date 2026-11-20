async page => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1600, height: 1050 })
  await page.addInitScript({ path: 'tests/workstation-fixture.js' })
  await page.addInitScript(() => {
    window.__workstationEmployees[0].state = 'idle'
    window.__memoryReads = []
    window.__journalReads = []
    window.officeDesktopBridge.listWorkLogDays = async () => ({ days: [{ date: '2026-11-18', count: 1 }], nextBefore: null })
    window.officeDesktopBridge.getWorkLogDay = async (employeeId, date) => {
      window.__journalReads.push({ employeeId, date })
      return { date, tasks: [{ id: 'work-one', source: 'delegation', task: '整理本周需求', startedAt: null,
        endedAt: null, recordedAt: '2026-11-18T03:00:00Z', steps: [], result: '已整理需求清单', issue: null, outcome: 'succeeded' }] }
    }
    window.__memoryResponse = {
      lastUpdated: '2026-11-17T01:00:00Z', user: {}, history: {},
      facts: Array.from({ length: 14 }, (_, index) => ({
        id: `memory-${index}`, content: ['界面修改只涉及前端，保留既有接口。', '完成后应说明实际验证范围。', '任务记录保留真实来源和执行状态。', '文档使用中文，避免过度兜底。'][index % 4] + ` 测试记录 ${index + 1}`,
        category: ['context', 'goal', 'correction', 'preference'][index % 4],
        source: `test-conversation-${index % 3}`, confidence: 0.9, createdAt: '2026-11-17T01:00:00Z',
      })),
    }
    window.__memoryResponse.facts[0].content += '\n\n' + '这是用于验证长记忆阅读的测试段落，保留换行并允许滚动查看完整内容。\n'.repeat(30)
    window.officeDesktopBridge.getEmployeeMemory = async employeeId => {
      window.__memoryReads.push(employeeId)
      if (window.__memoryError) throw new Error('Test memory read failed')
      return structuredClone(window.__memoryResponse)
    }
  })
  await page.goto(`http://127.0.0.1:5179/?memory-app=${Date.now()}#/office`)
  await page.bringToFront()
  await page.waitForFunction(() => window.game?.scene.getScene('game')?.myPlayer)
  await page.evaluate(() => {
    const scene = window.game.scene.getScene('game')
    scene.myPlayer.body.reset(-48, 573)
    scene.playerSelector.setPosition(-48, 541)
  })
  await page.waitForFunction(() => window.game.scene.getScene('game').playerSelector.selectedItem?.itemType === 0)
  await page.keyboard.press('e', { delay: 150 })
  await page.waitForFunction(() => window.game.scene.getScene('game').playerSelector.selectedItem?.itemType === 1)
  await page.keyboard.press('r', { delay: 150 })
  await page.locator('[data-artwork-ready="true"]').waitFor()
  const shortcut = page.getByRole('button', { name: '员工记忆', exact: true })
  if (await page.locator('.workstation-desktop-icon').count() !== 3) throw new Error('Missing separate shortcuts')
  await shortcut.hover()
  await page.screenshot({ path: 'output/playwright/memory-desktop.png' })
  await shortcut.click()
  await page.locator('[data-graph-ready="true"]').waitFor({ timeout: 30000 })
  if (await page.locator('.memory-app-graph canvas').count() === 0) throw new Error('No G6 canvas')
  await page.locator('.memory-app-list button').first().click()
  const memoryDialog = page.getByRole('dialog', { name: '记忆原文', exact: true })
  await memoryDialog.waitFor()
  if (await page.locator('.memory-app-detail .memory-app-original').count()) throw new Error('Original text still occupies sidebar')
  const popupSize = await memoryDialog.boundingBox()
  if (popupSize.width < 700) throw new Error('Original text dialog is too narrow')
  if (!(await page.locator('.memory-original-content').evaluate(element => element.scrollHeight > element.clientHeight))) throw new Error('Long original text must scroll')
  const appText = await page.locator('.employee-memory-app').innerText()
  if (appText.includes('不代表因果') || appText.includes('不代表事实已验证')) throw new Error('Removed disclaimers are still visible')
  if (!(await page.locator('.memory-app-footer').innerText()).startsWith('最后更新')) throw new Error('Missing update timestamp')
  await page.waitForTimeout(300)
  await page.screenshot({ path: 'output/playwright/employee-memory-graph.png' })
  const original = await page.locator('.memory-app-original').innerText()
  if (!original.includes('测试记录 1')) throw new Error('Missing full memory text')
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.waitForFunction(() => {
    const graph = document.querySelector('.memory-app-graph').getBoundingClientRect()
    const panel = document.querySelector('.memory-app-graph-panel').getBoundingClientRect()
    return Math.abs(graph.height - panel.height) < 2 && Math.abs(graph.width - panel.width) < 2
  })
  await page.screenshot({ path: 'output/playwright/memory-small.png' })
  const resizedPopup = await memoryDialog.boundingBox()
  if (resizedPopup.x < 0 || resizedPopup.x + resizedPopup.width > 1024 || resizedPopup.y + resizedPopup.height > 768) throw new Error('Original text dialog overflows viewport')
  await page.keyboard.press('Escape')
  await memoryDialog.waitFor({ state: 'hidden' })
  if (!(await page.locator('.employee-memory-app').isVisible())) throw new Error('Escape closed memory app instead of original text')
  await page.locator('.memory-app-list button').first().click()
  await memoryDialog.waitFor()
  await page.getByRole('button', { name: '关闭记忆原文' }).click()
  await memoryDialog.waitFor({ state: 'hidden' })
  await page.setViewportSize({ width: 1600, height: 1050 })
  await page.getByRole('button', { name: '按来源', exact: true }).click()
  await page.locator('[data-graph-ready="true"]').waitFor()
  await page.getByRole('button', { name: '放大', exact: true }).click()
  await page.getByRole('button', { name: '适应窗口', exact: true }).click()
  await page.getByRole('textbox', { name: '搜索记忆原文' }).fill('不存在的关键词')
  await page.getByText('没有匹配的记忆', { exact: true }).waitFor()
  await page.getByRole('button', { name: '清除筛选' }).click()
  await page.locator('[data-graph-ready="true"]').waitFor()
  await page.getByRole('button', { name: '显示更多' }).click()
  await page.waitForFunction(() => document.querySelector('.memory-app-graph')?.dataset.visibleFacts === '14')
  await page.locator('[data-graph-ready="true"]').waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.employee-memory-app').waitFor({ state: 'detached' })
  if (!(await page.getByRole('dialog').isVisible())) throw new Error('Escape closed the whole workstation')
  await shortcut.press('Enter')
  await page.locator('[data-graph-ready="true"]').waitFor()
  await page.evaluate(() => { window.__memoryResponse.facts = [] })
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await page.getByText('该员工暂无已记录的事实记忆', { exact: true }).waitFor()
  await page.evaluate(() => { window.__memoryError = true })
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await page.getByText('记忆读取失败', { exact: true }).waitFor()
  await page.getByRole('button', { name: '返回桌面' }).click()
  const reads = await page.evaluate(() => window.__memoryReads)
  if (reads.some(id => id !== 'workstation-preview-employee')) throw new Error('Wrong employee memory requested')
  await page.getByRole('button', { name: '工作记录', exact: true }).click()
  const file = page.getByRole('button', { name: '2026年11月18日工作日志', exact: true })
  await file.waitFor()
  if (await page.evaluate(() => window.__journalReads.length)) throw new Error('Folder prefetched journal details')
  await file.click()
  await page.getByText('已整理需求清单', { exact: true }).waitFor()
  const journal = await page.evaluate(() => window.__journalReads)
  if (journal.length !== 1 || journal[0].employeeId !== 'workstation-preview-employee' || journal[0].date !== '2026-11-18') throw new Error('Wrong journal read')
  await page.keyboard.press('Escape')
  await file.waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  await page.getByText('尚未接入', { exact: true }).waitFor()
  if (await page.locator('.employee-memory-app').count()) throw new Error('Placeholder opened memory')
  await page.getByRole('button', { name: '工作记录', exact: true }).click()
  await file.click()
  await page.getByText('已整理需求清单', { exact: true }).waitFor()
  await page.evaluate(async () => {
    window.__workstationEmployees[0].state = 'working'
    const { officeApi } = await import('/src/services/office/officeApi.ts')
    officeApi.invalidateEmployees()
    await officeApi.listEmployees({ fresh: true })
  })
  await page.locator('.workstation-preview__desktop').waitFor({ state: 'detached' })
  await page.locator('.work-log-app').waitFor({ state: 'detached' })
  await page.getByRole('button', { name: '关闭工作台', exact: true }).focus()
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  if (errors.length) throw new Error(errors.join('\n'))
  return { separateShortcuts: 3, singleClickOpens: true, originalTextDialog: true, longTextScrolls: true, g6Rendered: true, filterAndGrouping: true, emptyAndError: true, escapedToDesktop: true, workLogFolder: true, dayLoadedOnClick: true, closesWhenWorking: true, employeeScopedReads: reads.length, errors }
}
