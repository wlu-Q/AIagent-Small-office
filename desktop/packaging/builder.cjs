const path = require('node:path')
const root = path.resolve(__dirname, '../..')
const { packageLayout } = require('./layout.cjs')
const macRelease = process.env.OFFICE_MAC_RELEASE === '1'

module.exports = {
  appId: 'com.smalloffice.desktop',
  productName: '小小办公室',
  executableName: 'SmallOffice',
  directories: { app: path.join(root, 'output/packaging/app'), output: path.join(root, 'dist') },
  electronDist: path.join(root, 'node_modules/electron/dist'),
  electronVersion: require('../../node_modules/electron/package.json').version,
  asar: true,
  npmRebuild: false,
  afterPack: async ({ appOutDir, electronPlatformName, arch }) => {
    // The local Electron runtime can accumulate Chromium logs during development.
    const expected = packageLayout(root, electronPlatformName, require('builder-util').Arch[arch]).appOutDir
    if (path.resolve(appOutDir) !== expected) throw new Error('Unexpected release output directory')
    require('node:fs').rmSync(path.join(appOutDir, 'debug.log'), { force: true })
  },
  files: ['package.json', 'desktop/**/*', 'contracts/**/*', 'client/dist/**/*', 'node_modules/i18next/**/*'],
  extraResources: [{ from: path.join(root, 'output/packaging/resources'), to: 'office',
    filter: ['**/*', '!__pycache__/**', '!backend/**/__pycache__/**', '!skills/**/__pycache__/**'] }],
  win: { target: [{ target: 'nsis', arch: ['x64'] }], signExecutable: false },
  mac: {
    target: [{ target: 'dmg', arch: ['arm64'] }],
    category: 'public.app-category.productivity',
    minimumSystemVersion: '15.0.0',
    icon: path.join(root, 'docs/assets/branding/smo-logo.png'),
    hardenedRuntime: true,
    // macOS grants access to protected folders only when the purpose string
    // exists. Without these keys the system denies Desktop/Documents/Downloads
    // silently, so a project bound there makes every file tool fail with EPERM.
    extendInfo: {
      NSDesktopFolderUsageDescription: '读取并修改你在桌面中绑定的项目目录。',
      NSDocumentsFolderUsageDescription: '读取并修改你在文稿中绑定的项目目录。',
      NSDownloadsFolderUsageDescription: '读取你在下载中绑定的项目目录。',
      NSRemovableVolumesUsageDescription: '读取你在外接磁盘中绑定的项目目录。',
    },
    ...(macRelease ? {} : { identity: '-' }),
    notarize: macRelease,
    artifactName: macRelease ? 'SmallOffice-${version}-mac-${arch}.${ext}' : 'SmallOffice-${version}-mac-${arch}-local.${ext}',
  },
  ...(macRelease ? { forceCodeSigning: true } : {}),
  dmg: {
    contents: [{ x: 150, y: 170, type: 'file' }, { x: 430, y: 170, type: 'link', path: '/Applications' }],
  },
  nsis: { oneClick: false, perMachine: false, allowToChangeInstallationDirectory: true,
    include: path.join(__dirname, 'uninstall.nsh'),
    createDesktopShortcut: true, createStartMenuShortcut: true, shortcutName: '小小办公室',
    runAfterFinish: false },
  artifactName: 'SmallOffice-Setup-${version}-${arch}.${ext}',
  compression: 'normal',
  publish: null,
}
