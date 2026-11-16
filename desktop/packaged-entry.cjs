// Installed-app entry point. Business services remain in the existing main/Gateway.
const { app, dialog } = require('electron')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')
const { runtimePaths, runtimeEnvironment } = require('./runtime-paths.cjs')

async function main() {
  const requestedData = app.commandLine.getSwitchValue('user-data-dir')
  const profile = requestedData ? path.resolve(requestedData) : path.join(app.getPath('appData'), 'SmallOffice')
  fs.mkdirSync(profile, { recursive: true })
  app.setPath('userData', profile)
  const resources = path.join(process.resourcesPath, 'office')
  const data = path.join(app.getPath('userData'), 'office')
  fs.mkdirSync(data, { recursive: true })
  // The installed app always owns its bundled Gateway, never a developer instance.
  for (const name of Object.keys(process.env)) {
    if (name.startsWith('DEER_FLOW_') || name === 'OFFICE_RENDERER_URL' || name === 'PYTHONHOME'
      || name === 'PYTHONPATH' || name === 'OFFICE_LIBRARY_API_KEY') delete process.env[name]
  }
  Object.assign(process.env, {
    OFFICE_PACKAGED_RESOURCES: resources,
    DEER_FLOW_HOME: path.join(data, 'state'),
    DEER_FLOW_CONFIG_PATH: path.join(data, 'config.yaml'),
    DEER_FLOW_EXTENSIONS_CONFIG_PATH: path.join(data, 'extensions_config.json'),
    DEER_FLOW_PROJECT_ROOT: data,
    DEER_FLOW_SKILLS_PATH: path.toNamespacedPath(path.join(resources, 'skills')),
    OFFICE_POWERSHELL_EXECUTABLE: runtimePaths(resources).powershell,
    PYTHONNOUSERSITE: '1',
    PYTHONDONTWRITEBYTECODE: '1',
  })
  const environment = runtimeEnvironment(resources)
  if (process.platform === 'win32') {
    for (const name of Object.keys(process.env)) if (name.toLowerCase() === 'path') delete process.env[name]
  }
  Object.assign(process.env, environment)
  // Allocate one loopback endpoint per installation; main checks it before spawning.
  process.env.OFFICE_MANAGED_GATEWAY_PORT = String(await new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const port = probe.address().port
      probe.close(error => error ? reject(error) : resolve(port))
    })
  }))
  require('./main.cjs')
}

main().catch(error => {
  app.whenReady().then(() => {
    dialog.showErrorBox('小小办公室启动失败', error.message)
    app.exit(1)
  })
})
