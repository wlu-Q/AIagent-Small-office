const fs = require('node:fs')
const path = require('node:path')
const i18next = require('i18next')
const en = require('../contracts/locales/en.json')
const zh = require('../contracts/locales/zh-CN.json')

const i18n = i18next.createInstance()
i18n.init({ resources: { en: { translation: en }, 'zh-CN': { translation: zh } }, lng: 'en',
  supportedLngs: ['en', 'zh-CN'], fallbackLng: false, showSupportNotice: false, keySeparator: false, initAsync: false, interpolation: { escapeValue: false } })

function createLocalization(directory, systemLocale) {
  const file = path.join(directory, 'language.json')
  let language = systemLocale.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
  if (fs.existsSync(file)) {
    language = JSON.parse(fs.readFileSync(file, 'utf8')).language
    if (!['en', 'zh-CN'].includes(language)) throw new Error('Invalid saved language')
  }
  i18n.changeLanguage(language)
  return {
    getLanguage: () => language,
    t: (key, params) => i18n.t(key, params),
    setLanguage(next) {
      if (!['en', 'zh-CN'].includes(next)) throw new Error('Unsupported language')
      fs.mkdirSync(directory, { recursive: true })
      const temporary = file + '.tmp'
      try {
        fs.writeFileSync(temporary, JSON.stringify({ language: next }), 'utf8')
        fs.renameSync(temporary, file)
      } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary) }
      language = next
      i18n.changeLanguage(next)
      return next
    },
  }
}
module.exports = { createLocalization, t: (key, params) => i18n.t(key, params) }
