// Production Desktop/preload/Gateway/native employees; deterministic model choices only.
const assert = require('node:assert/strict')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

exports.run = async ({ window, api, send, waitFor }) => {
  const evaluate = expression => window.webContents.executeJavaScript(expression)
  const pmRuns = async () => (await api('/api/office/employees/pm/runs?conversationKind=group')).runs
  const waitIdle = async (before, expectedCount) => {
    const deadline = Date.now() + 90000
    let record
    while (Date.now() < deadline) {
      record = (await pmRuns()).find(r => !before.includes(r.runId))
      if (record?.eventsFinalized && !record.finalizing) break
      await sleep(100)
    }
    assert.equal(record?.status, 'success', record?.error || JSON.stringify(record))
    assert.ok(record.eventsFinalized && !record.finalizing)
    const { events } = await api(`/api/office/employees/pm/runs/${record.runId}/events`)
    assert.equal(events.filter(e => e.event_type === 'subagent.start').length, expectedCount)
    return { record, events }
  }

  const before = (await pmRuns()).map(r => r.runId)
  await send('E2E_LIMIT_PARALLEL 请安排几位员工分别分析，结果汇总给我。')
  let peak = 0
  let fiveWorking = false
  const deadline = Date.now() + 20000
  while (Date.now() < deadline) {
    const employees = (await api('/api/office/employees')).employees
    const working = employees.filter(e => e.id !== 'pm' && e.state === 'working')
    peak = Math.max(peak, working.length)
    if (working.length === 5) {
      assert.equal(employees.find(e => e.id === 'writer6').state, 'idle', 'sixth rejected call is not secretly queued')
      fiveWorking = true
      break
    }
    await sleep(50)
  }
  assert.ok(fiveWorking, `expected five real delegated employees, observed ${peak}`)
  // Private messages to an employee occupied by PM must still enter its FIFO.
  await evaluate("document.querySelector('[data-office-employee=\"writer\"]').click()")
  await send('E2E_EMPLOYEE 忙完以后再回复我。')
  await waitFor("document.body.innerText.includes('排队中')", 'private FIFO remains visible during delegation')
  const queued = (await api('/api/office/queue')).items
  assert.ok(queued.some(item => item.targetEmployeeId === 'writer'))
  await evaluate("document.querySelector('[data-office-group]').click()")
  const parallel = await waitIdle(before, 6)
  assert.ok(JSON.stringify(parallel.events).includes('not started or queued'), 'partial limit is explicit in the actual Run')
  assert.ok(JSON.stringify(parallel.events).includes('LIMIT_CHILD_DONE'), 'real employee reports are returned')
  await waitFor("document.body.innerText.includes('E2E_LIMIT_PARALLEL_DONE')", 'PM summary visible')

  // The already-queued direct request completes after the employee is released.
  const privateDeadline = Date.now() + 20000
  let privateRuns
  do {
    privateRuns = (await api('/api/office/employees/writer/runs?conversationKind=private')).runs
    if (privateRuns.some(r => r.status === 'success' && r.eventsFinalized)) break
    await sleep(100)
  } while (Date.now() < privateDeadline)
  assert.ok(privateRuns.some(r => r.status === 'success' && r.eventsFinalized))

  // Twenty actual task invocations in four turns; the twenty-first must not start.
  const totalBefore = (await pmRuns()).map(r => r.runId)
  await send('E2E_LIMIT_TOTAL 请继续分批核对这些条目。')
  const total = await waitIdle(totalBefore, 20)
  await waitFor("document.body.innerText.includes('SUBAGENT LIMIT REACHED')", 'total limit visibly reported')
  assert.ok(JSON.stringify(total.events).includes('SUBAGENT LIMIT REACHED'))
  assert.equal((await api('/api/office/queue')).items.length, 0)
  assert.equal((await api('/api/office/group/execution')).runTargets.length, 0)
  assert.ok((await api('/api/office/employees')).employees.every(e => e.state === 'idle'))
  console.log('DELEGATION_LIMITS_UI_OK', JSON.stringify({ parallelRun: parallel.record.runId, totalRun: total.record.runId, peak, actualDelegations: [6, 20], privateFifo: 'drained' }))
}
