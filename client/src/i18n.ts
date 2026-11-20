import i18next from 'i18next'
import { initReactI18next, useTranslation } from 'react-i18next'
import en from '../../contracts/locales/en.json'
import zh from '../../contracts/locales/zh-CN.json'

export type Language = 'en' | 'zh-CN'
export const i18n = i18next.createInstance()
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, 'zh-CN': { translation: zh } },
  lng: 'en', supportedLngs: ['en', 'zh-CN'], fallbackLng: false, showSupportNotice: false, keySeparator: false,
  initAsync: false, interpolation: { escapeValue: false },
})
export function t(key: string, params?: Record<string, unknown>): string {
  return i18n.t(key, params) as string
}
export function useLocale() { useTranslation(undefined, { i18n }); return i18n.language as Language }
export async function initializeLocale() {
  const language = window.officeDesktopBridge
    ? await window.officeDesktopBridge.getLanguage()
    : navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
  await i18n.changeLanguage(language)
  document.documentElement.lang = language
  document.title = t('app.title')
}
export async function changeLanguage(language: Language) {
  if (window.officeDesktopBridge) await window.officeDesktopBridge.setLanguage(language)
  await i18n.changeLanguage(language)
  document.documentElement.lang = language
  document.title = t('app.title')
}
export function formatDate(value: string) {
  return new Intl.DateTimeFormat(i18n.language, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))
}

export const providerLabel = (id: string | null, name: string) => !id ? t('ui.historical_configuration') : (id === 'doubao' || id === 'qwen') ? t(`providers.${id}`) : name
export const thinkingLabel = (level?: string | null) => level ? t(`thinking.${level}`) : t('ui.model_default')

export function localizedTemplate<T extends { templateId: string; role: string; category: string; tags: string[]; specialties: string[]; prompt?: string }>(template: T): T {
  const fields = i18n.getResource(i18n.language, 'translation', `templates.${template.templateId}.role`)
  if (typeof fields !== 'string') throw new Error('Unknown employee template')
  const resource = (field: string) => i18n.getResource(i18n.language, 'translation', `templates.${template.templateId}.${field}`)
  return { ...template, role: resource('role'), category: resource('category'), tags: resource('tags'), specialties: resource('specialties'), prompt: resource('prompt') }
}

export function roleLabel(employee: { templateId: string; role: string }) {
  return t(`templates.${employee.templateId}.role`)
}
