import { useState } from 'react'
import { changeLanguage, useLocale, type Language } from '../i18n'

export default function LanguageSelect() {
  const language = useLocale()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const select = async (next: Language) => {
    setBusy(true); setError('')
    try { await changeLanguage(next) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Language setting failed') }
    finally { setBusy(false) }
  }
  return <div style={{ color: '#526b82', fontSize: 13 }}>
    <label>Language / 语言 <select data-office-language aria-label="Language / 语言" value={language} disabled={busy}
      onChange={event => void select(event.target.value as Language)} style={{ padding: '6px 10px', color: '#253b51', background: '#fff', border: '1px solid #c7d8df', borderRadius: 8 }}>
      <option value="en">English</option><option value="zh-CN">简体中文</option>
    </select></label>
    {error && <p role="alert">{error}</p>}
  </div>
}
