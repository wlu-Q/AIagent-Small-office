const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

exports.run = async ({ window, api, send, waitFor }) => {
  const evaluate = expression => window.webContents.executeJavaScript(expression)
  const button = text => `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(text)})`
  const output = path.resolve(__dirname, '../output/playwright')
  fs.mkdirSync(output, { recursive: true })
  await waitFor("document.querySelector('[data-office-employee=\"writer\"]')", 'delivery employee loaded')
  await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
  await send('E2E_EMPLOYEE E2E_DELIVERY_REJECT 试一下这个命令，我会拒绝。')
  await evaluate("(() => { const input = document.querySelector('[data-office-message-input]'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '这条草稿先保留'); input.dispatchEvent(new Event('input', { bubbles: true })) })()")
  await waitFor("document.querySelector('[data-approval-command]')?.textContent.includes('rejected.txt')", 'specific approval')
  assert.equal(await evaluate("document.querySelector('[data-execution-approval]').closest('[role=\"dialog\"]') !== null"), false)
  assert.equal(await evaluate("!!document.querySelector('[data-office-message-input]').closest('[hidden]')"), true)
  fs.writeFileSync(path.join(output, 'stage5-approval.png'), (await window.webContents.capturePage()).toPNG())
  await evaluate(`${button('拒绝')}.click()`)
  await waitFor("document.body.innerText.includes('E2E_DELIVERY_REJECT_DONE')", 'rejection returned to agent')
  await waitFor("!document.querySelector('[data-office-message-input]').closest('[hidden]')", 'composer restored')
  assert.equal(await evaluate("document.querySelector('[data-office-message-input]').value"), '这条草稿先保留')
  const rejectedFacts = await api('/test-workspaces')
  assert.equal(rejectedFacts.source, '# 临时联调项目\n')
  for (const item of rejectedFacts.workspaces) if (item.root_path) assert.equal(fs.existsSync(path.join(item.root_path, 'rejected.txt')), false)

  // Group PM delegates; while its child waits, a direct message must stay FIFO.
  await evaluate("document.querySelector('[data-office-group]').click()")
  await send('E2E_DELIVERY_TERMINAL 帮我让员工修改文档、检查后交付。')
  await waitFor("document.querySelector('[data-approval-command]')?.textContent.includes('TERMINAL_DELIVERED')", 'delegated command approval')
  // Office consumes persisted progress. A child waiting for approval must be
  // visible before it ends, even with fewer than 25 events.
  const pendingPm = (await api('/api/office/employees/pm/runs?conversationKind=group')).runs[0]
  const progressDeadline = Date.now() + 5000
  let progress = []
  do {
    progress = (await api(`/api/office/employees/pm/runs/${pendingPm.runId}/events`)).events
    if (progress.some(e => e.event_type === 'subagent.start')) break
    await new Promise(resolve => setTimeout(resolve, 100))
  } while (Date.now() < progressDeadline)
  assert.equal(pendingPm.status, 'running')
  assert.ok(progress.some(e => e.event_type === 'subagent.start'), 'active child persisted before approval')
  assert.ok(!progress.some(e => e.event_type === 'subagent.end'), 'child is still waiting')
  await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
  await waitFor("document.querySelector('[data-office-chat-main] > header')?.textContent.includes('私聊') && !document.querySelector('[data-approval-command]')", 'delegated approval belongs to PM group, not employee private chat')
  assert.equal(await evaluate("document.body.innerText.includes('其他会话有')"), false)
  await evaluate("document.querySelector('[data-office-group]').click()")
  await waitFor("document.querySelector('[data-approval-command]')?.textContent.includes('TERMINAL_DELIVERED')", 'group approval restored')
  const queued = await api('/api/office/messages', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
    text: 'E2E_EMPLOYEE 请回复收到', conversationKind: 'private', targetEmployeeId: 'writer', messageId: 'stage5-queued', mentionEmployeeIds: [] }) })
  assert.ok(queued)
  assert.equal((await api('/api/office/queue')).items.length, 1)
  assert.ok((await api('/api/office/employees')).employees.find(e => e.id === 'writer').state === 'working')
  await evaluate(`${button('允许一次')}.click()`)
  await waitFor("document.querySelector('[data-approval-command]')?.textContent.includes('Get-Content')", 'validation approval')
  await evaluate(`${button('允许一次')}.click()`)
  await waitFor("document.querySelector('[data-approval-command]')?.textContent.includes('写回工作区改动')", 'publication approval')
  await evaluate(`${button('允许一次')}.click()`)
  await waitFor("document.body.innerText.includes('E2E_DELIVERY_TERMINAL_DONE')", 'PM delivery returned', 60000)
  assert.match((await api('/test-workspaces')).source, /TERMINAL_DELIVERED/)

  // Full access is chosen in the actual settings UI and requires confirmation.
  await evaluate("document.querySelector('[aria-label=\"打开办公室设置\"]').click()")
  await waitFor("document.querySelector('input[value=\"full_access\"]')?.disabled === false", 'settings loaded')
  await evaluate("document.querySelector('input[value=\"full_access\"]').click()")
  await waitFor("document.body.innerText.includes('开启完全访问？')", 'risk confirmation')
  // Real mousedown bubbling used to close the entire settings overlay.
  await evaluate("(() => { const b = [...document.querySelector('#full-access-title').closest('[role=dialog]').querySelectorAll('button')].find(b => b.textContent === '取消'); b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); b.click() })()")
  await waitFor("!document.querySelector('#full-access-title') && !!document.querySelector('[data-office-settings-dialog]')", 'cancel warning preserves settings')
  assert.equal(await evaluate("document.querySelector('input[value=\"approval\"]').checked"), true)
  await evaluate("document.querySelector('input[value=\"full_access\"]').click()")
  await waitFor("!!document.querySelector('#full-access-title')", 'reopen warning')
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'ESC' })
  window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'ESC' })
  await waitFor("!document.querySelector('#full-access-title') && !!document.querySelector('[data-office-settings-dialog]')", 'escape warning preserves settings')
  await evaluate("document.querySelector('input[value=\"full_access\"]').click()")
  await waitFor("!!document.querySelector('#full-access-title')", 'open warning to confirm')
  fs.writeFileSync(path.join(output, 'stage5-full-access.png'), (await window.webContents.capturePage()).toPNG())
  await evaluate(`${button('了解风险，继续')}.click()`)
  await evaluate(`${button('保存设置')}.click()`)
  await waitFor("!document.querySelector('[data-office-settings-dialog]')", 'settings saved')
  assert.equal((await api('/api/office/settings')).terminalAccessMode, 'full_access')
  // Continue one real workspace and merge the other with native Git.
  await send('E2E_DELIVERY_PM 请两位员工分别改同一处，再把两份需求整合测试后交付。')
  await waitFor("document.body.innerText.includes('E2E_DELIVERY_PM_DONE')", 'parallel integration delivery', 90000)
  const facts = await api('/test-workspaces')
  assert.equal(facts.source.trim(), '# A + B')
  const integrated = facts.workspaces.find(w => w.root_path && fs.readFileSync(path.join(w.root_path, 'README.md'), 'utf8').trim() === '# A + B')
  assert.ok(integrated)
  assert.equal('integration_status' in integrated, false)
  const { execFileSync } = require('node:child_process')
  assert.equal(execFileSync('git', ['-C', integrated.root_path, 'ls-files', '-u'], { encoding: 'utf8', windowsHide: true }), '')
  assert.ok(execFileSync('git', ['-C', integrated.root_path, 'rev-list', '--merges', 'HEAD'], { encoding: 'utf8', windowsHide: true }).trim())
  assert.equal((await api('/api/office/execution-approvals')).items.length, 0)
  assert.equal((await api('/api/office/queue')).items.length, 0)
  fs.writeFileSync(path.join(output, 'stage3-delivery.json'), JSON.stringify(facts, null, 2))
  fs.writeFileSync(path.join(output, 'stage3-delivery.png'), (await window.webContents.capturePage()).toPNG())
  console.log('DELIVERY_UI_OK')
}
