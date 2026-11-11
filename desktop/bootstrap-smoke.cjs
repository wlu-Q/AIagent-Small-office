const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { dialog } = require('electron')

async function run({ window, api, waitFor, endpoint, profile, reloadRenderer }) {
  const project = path.join(profile, '中文 工作区')
  fs.mkdirSync(project)
  const original = dialog.showOpenDialog
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [project] })
  const setValue = (selector, value) => window.webContents.executeJavaScript(`(() => {
    const input = document.querySelector(${JSON.stringify(selector)});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`)
  try {
    await waitFor("document.querySelector('[data-bootstrap-bind]')", 'first-run workspace picker')
    assert.equal((await api('/api/office/project')).workspace, null)
    await setValue('[data-bootstrap-company]', '初始化联调')
    await setValue('[data-bootstrap-model]', 'gpt-test')
    await setValue('[data-bootstrap-base-url]', `${endpoint.origin}/test-model/v1`)
    await setValue('[data-bootstrap-api-key]', 'local-fixture-key')
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-test]').click()")
    await waitFor("document.body.innerText.includes('连接成功')", 'real HTTP model connection')
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-enter]').disabled"), true)
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-bind]').click()")
    await waitFor("document.querySelector('[data-bootstrap-enter]').disabled === false", 'persisted workspace enables entry')
    const binding = (await api('/api/office/project')).workspace
    assert.equal(binding.path, project)
    await window.webContents.executeJavaScript("document.querySelector('[data-bootstrap-enter]').click()")
    await waitFor("location.hash === '#/office' && document.querySelector('canvas')", 'initialized Office opens')
    assert.equal((await api('/api/office/bootstrap')).initialized, true)
    assert.equal((await api('/api/office/project/context')).context.path, project)
    await reloadRenderer()
    await waitFor("document.querySelector('canvas') && !document.querySelector('[data-office-bootstrap]')", 'initialized Office survives reload')
    assert.equal((await api('/api/office/project')).workspace.projectId, binding.projectId)
    assert.deepEqual(fs.readdirSync(project), [])
    console.log('LIVE_BOOTSTRAP_WORKSPACE_OK')
  } finally {
    dialog.showOpenDialog = original
  }
}

module.exports = { run }
