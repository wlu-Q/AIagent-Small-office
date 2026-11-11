const path = require('node:path')

const packagedResources = process.env.OFFICE_PACKAGED_RESOURCES
// Keep the extended prefix when Python resolves SUBST drives or deep installs.
const backendRoot = packagedResources ? path.toNamespacedPath(path.join(packagedResources, 'backend')) : path.resolve(__dirname, '..', 'backend')
const packagedPython = packagedResources ? path.join(packagedResources, 'python', 'python.exe') : null
const runtimeHome = process.env.DEER_FLOW_HOME || path.join(backendRoot, '.deer-flow')

function gatewayEnv(host, port) {
  return {
    ...process.env,
    OFFICE_POWERSHELL_EXECUTABLE: process.env.OFFICE_POWERSHELL_EXECUTABLE || path.join(
      packagedResources || path.resolve(__dirname, '../output/packaging/resources'), 'powershell', 'pwsh.exe'),
    ...(packagedResources ? { PYTHONHOME: path.toNamespacedPath(path.join(packagedResources, 'python')) } : {}),
    DEER_FLOW_AUTH_DISABLED: process.env.DEER_FLOW_AUTH_DISABLED ?? '1',
    GATEWAY_HOST: host,
    GATEWAY_PORT: port,
    DEER_FLOW_PROJECT_ROOT: packagedResources ? process.env.DEER_FLOW_PROJECT_ROOT : path.dirname(backendRoot),
    DEER_FLOW_CONFIG_PATH: process.env.DEER_FLOW_CONFIG_PATH || path.join(backendRoot, '..', 'config.yaml'),
    DEER_FLOW_HOME: runtimeHome,
    SMALL_OFFICE_USER_ID: 'default',
    // Prefer the checkout over an installed copy without editable .pth encoding issues.
    PYTHONPATH: [backendRoot, path.join(backendRoot, 'packages', 'harness'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PYTHONUTF8: '1',
    PYTHONFAULTHANDLER: '1',
  }
}

module.exports = { backendRoot, gatewayEnv, packagedPython, packagedResources, runtimeHome }
