const path = require('node:path')
const root = path.resolve(__dirname, '../..')

module.exports = {
  appId: 'com.smalloffice.desktop',
  productName: '小小办公室',
  executableName: 'SmallOffice',
  directories: { app: path.join(root, 'output/packaging/app'), output: path.join(root, 'dist') },
  electronDist: path.join(root, 'node_modules/electron/dist'),
  electronVersion: require('../../node_modules/electron/package.json').version,
  asar: true,
  npmRebuild: false,
  afterPack: async ({ appOutDir }) => {
    // The local Electron runtime can accumulate Chromium logs during development.
    const expected = path.join(root, 'dist', 'win-unpacked')
    if (path.resolve(appOutDir) !== expected) throw new Error('Unexpected release output directory')
    require('node:fs').rmSync(path.join(appOutDir, 'debug.log'), { force: true })
  },
  files: ['package.json', 'desktop/**/*', 'contracts/**/*', 'client/dist/**/*', 'node_modules/i18next/**/*'],
  extraResources: [{ from: path.join(root, 'output/packaging/resources'), to: 'office',
    filter: ['**/*', '!__pycache__/**', '!backend/**/__pycache__/**', '!skills/**/__pycache__/**'] }],
  win: { target: [{ target: 'nsis', arch: ['x64'] }], signExecutable: false },
  nsis: { oneClick: false, perMachine: false, allowToChangeInstallationDirectory: true,
    include: path.join(__dirname, 'uninstall.nsh'),
    createDesktopShortcut: true, createStartMenuShortcut: true, shortcutName: '小小办公室',
    runAfterFinish: false },
  artifactName: 'SmallOffice-Setup-${version}-${arch}.${ext}',
  compression: 'normal',
  publish: null,
}
