const i18n = require('i18next').createInstance()
i18n.init({ resources: {
  en: { translation: require('../contracts/locales/en.json') },
  'zh-CN': { translation: require('../contracts/locales/zh-CN.json') },
}, lng: 'zh-CN', fallbackLng: false, keySeparator: false, showSupportNotice: false, initAsync: false })
module.exports = { i18n, t: (key, params) => i18n.t(key, params) }
