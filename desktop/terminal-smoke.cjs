const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

exports.run = async ({ window, api, send, waitFor }) => {
  const evaluate = expression => window.webContents.executeJavaScript(expression)
  await send('E2E_TERMINAL_PM 帮我让员工试一下后台运行，再生成一个文本文件给我。')
  await waitFor("document.body.innerText.includes('E2E_TERMINAL_PM_DONE')", 'delegated terminal completed', 60000)
  assert.match(await evaluate('document.body.innerText'), /E2E_TERMINAL_DONE/)
  await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
  await send('E2E_EMPLOYEE E2E_TERMINAL_DIRECT 用终端生成个文件，后台任务记得收掉。')
  await waitFor("document.body.innerText.includes('E2E_TERMINAL_DONE')", 'private terminal completed', 60000)
  const runs = (await api('/api/office/employees/writer/runs?conversationKind=private')).runs
  let run
  const runDeadline = Date.now() + 30000
  do {
    run = await api(`/api/office/employees/writer/runs/${runs[0].runId}`)
    if (!['pending', 'running'].includes(run.status) && !run.finalizing && run.eventsFinalized !== false) break
    await new Promise(resolve => setTimeout(resolve, 150))
  } while (Date.now() < runDeadline)
  assert.equal(run.status, 'success')
  assert.equal(run.finalizing, false)
  assert.equal(run.eventsFinalized, true)
  const events = await api(`/api/office/employees/writer/runs/${run.runId}/events?limit=2000`)
  for (const name of ['powershell_run', 'powershell_list', 'powershell_stop', 'powershell_read']) assert.ok(JSON.stringify(events).includes(name))
  await waitFor("document.querySelector('[data-office-file]:not(:disabled)')", 'command output file with Run ownership')
  await evaluate("document.querySelector('[data-office-file]:not(:disabled)').click()")
  await waitFor("document.querySelector('[data-office-file-preview]')?.innerText.includes('终端任务完成')", 'actual terminal file preview')
  let facts
  const deadline = Date.now() + 15000
  do {
    facts = await api('/test-workspaces')
    if (facts.workspaces.length === 0 &&
        (await api('/api/office/employees')).employees.every(e => e.state === 'idle')) break
    await new Promise(resolve => setTimeout(resolve, 150))
  } while (Date.now() < deadline)
  assert.equal(facts.source, '# 临时联调项目\n')
  assert.match(facts.status, /shell-result.txt/)
  assert.equal(facts.workspaces.length, 0)
  assert.ok((await api('/api/office/employees')).employees.every(e => e.state === 'idle'))
  assert.equal((await api('/api/office/queue')).items.length, 0)
  const output = path.resolve(__dirname, '../output/playwright')
  fs.mkdirSync(output, { recursive: true })
  fs.writeFileSync(path.join(output, 'stage3-terminal.png'), (await window.webContents.capturePage()).toPNG())
  fs.writeFileSync(path.join(output, 'stage3-terminal.json'), JSON.stringify({ run, events, facts }, null, 2))
  console.log('TERMINAL_UI_OK')
}
