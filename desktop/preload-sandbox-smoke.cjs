const assert = require('node:assert/strict')
const path = require('node:path')
const { app, BrowserWindow } = require('electron')

const expectedMethods = [
  'listExecutionApprovals',
  'decideExecutionApproval',
  'listMcpServices', 'changeMcpService', 'getOfficeEmployeeTools',
  'saveOfficeEmployeeTools',
  'officeLibrary',
  'getCurrentOfficeWorkflow',
  'getOfficeWorkflow',
  'getOfficeGroupExecution',
  'pauseOfficeGroup',
  'resetOfficeGroup',
  'cancelOfficeRun',
  'createMemoryFact',
  'createOfficeEmployee',
  'deleteMemoryFact',
  'deleteOfficeEmployee',
  'deleteOfficeQueue',
  'editOfficeQueue',
  'getOfficeBootstrap',
  'getOfficeEmployee',
  'getOfficeRun',
  'getOfficeSettings',
  'getLanguage',
  'setLanguage',
  'getServiceStatus',
  'getUserMemory',
  'getEmployeeMemory',
  'listWorkLogDays',
  'getWorkLogDay',
  'getWorkspaceSummary',
  'getZoomFactor',
  'initializeOfficeBootstrap',
  'listModels',
  'listOfficeModels',
  'getOfficeModel',
  'updateOfficeModel',
  'addOfficeModel',
  'setOfficeDefaultModel',
  'listOfficeConversationMessages',
  'listOfficeEmployeeTemplates',
  'listOfficeEmployees',
  'listOfficeGroupMessages',
  'listOfficeQueue',
  'listOfficeRunEvents',
  'listOfficeRuns',
  'listSkills',
  'onServiceEvent',
  'openExternalUrl',
  'pingService',
  'previewOfficeFile',
  'resetZoom',
  'resumeOfficeRun',
  'revealOfficeFile',
  'saveOfficeEmployeePersonalInfo',
  'saveOfficeSettings',
  'selectAndBindWorkspace',
  'sendOfficeMessage',
  'setZoomFactor',
  'testOfficeBootstrapModel',
  'updateMemoryFact',
  'zoomBy',
]

async function run() {
  await app.whenReady()
  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  try {
    await window.loadURL('data:text/html;charset=utf-8,<html><body>preload smoke</body></html>')
    const methods = await window.webContents.executeJavaScript(
      'Object.keys(globalThis.officeDesktopBridge || {}).sort()',
    )
    const hasRendererRequire = await window.webContents.executeJavaScript(
      "typeof globalThis.require !== 'undefined'",
    )
    assert.deepEqual(methods, [...expectedMethods].sort())
    assert.equal(hasRendererRequire, false)
    console.log('PRELOAD_SANDBOX_SMOKE_OK')
  } finally {
    if (!window.isDestroyed()) window.destroy()
  }
}

run().then(
  () => app.exit(0),
  (error) => {
    console.error(error)
    app.exit(1)
  },
)
