const path = require('node:path')

function packageLayout(root, platform = process.platform, arch = process.arch) {
  const windows = platform === 'win32' && arch === 'x64'
  const mac = platform === 'darwin' && arch === 'arm64'
  if (!windows && !mac) throw new Error(`Packaging requires native Windows x64 or macOS arm64; found ${platform}/${arch}`)
  const appOutDir = path.join(root, 'dist', windows ? 'win-unpacked' : 'mac-arm64')
  const app = mac ? path.join(appOutDir, 'SmallOffice.app') : appOutDir
  return {
    appOutDir, app,
    resources: path.join(app, mac ? 'Contents/Resources' : 'resources'),
    executable: path.join(app, mac ? 'Contents/MacOS/SmallOffice' : 'SmallOffice.exe'),
    electron: path.join(root, 'node_modules/electron/dist', mac ? 'Electron.app/Contents/MacOS/Electron' : 'electron.exe'),
    builderArgs: mac ? ['--mac', '--arm64'] : ['--win', '--x64'],
    manifest: mac ? 'runtimes.macos.json' : 'runtimes.json',
  }
}

module.exports = { packageLayout }
