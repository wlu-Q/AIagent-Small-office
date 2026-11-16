const path = require('node:path')

function runtimePaths(resources, platform = process.platform) {
  const paths = platform === 'win32' ? path.win32 : path.posix
  if (platform !== 'win32' && platform !== 'darwin') throw new Error(`Unsupported desktop platform: ${platform}`)
  const windows = platform === 'win32'
  return {
    python: paths.join(resources, 'python', windows ? 'python.exe' : 'bin/python3'),
    node: paths.join(resources, 'node', windows ? 'node.exe' : 'bin/node'),
    git: paths.join(resources, 'git', windows ? 'cmd/git.exe' : 'bin/git'),
    powershell: paths.join(resources, 'powershell', windows ? 'pwsh.exe' : 'pwsh'),
    bins: windows
      ? ['python', 'python/Scripts', 'node', 'git/cmd', 'powershell'].map(name => paths.join(resources, name))
      : ['python/bin', 'node/bin', 'git/bin', 'powershell'].map(name => paths.join(resources, name)),
  }
}

function developmentPython(backendRoot, platform = process.platform) {
  const paths = platform === 'win32' ? path.win32 : path.posix
  return paths.join(backendRoot, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
}

function runtimeEnvironment(resources, environment = process.env, platform = process.platform) {
  const result = { ...environment }
  const windows = platform === 'win32'
  const key = windows ? 'Path' : 'PATH'
  const previous = windows ? result.Path || result.PATH || '' : result.PATH || ''
  if (windows) {
    for (const name of Object.keys(result)) if (name.toLowerCase() === 'path') delete result[name]
  }
  result[key] = [...runtimePaths(resources, platform).bins, previous].filter(Boolean).join(windows ? ';' : ':')
  return result
}

module.exports = { runtimePaths, developmentPython, runtimeEnvironment }
