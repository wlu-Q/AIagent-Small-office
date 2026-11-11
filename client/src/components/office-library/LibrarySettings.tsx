import { t, useLocale } from '../../i18n'
import React, { useCallback, useEffect, useState } from 'react'
import styled from '@emotion/styled'
import Dialog from '@mui/material/Dialog'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import IconButton from '@mui/material/IconButton'
import CloseRounded from '@mui/icons-material/CloseRounded'
import TuneRounded from '@mui/icons-material/TuneRounded'
import VisibilityOutlined from '@mui/icons-material/VisibilityOutlined'
import VisibilityOffOutlined from '@mui/icons-material/VisibilityOffOutlined'
import { libraryApi, LibrarySettings as Settings } from './libraryApi'
import { pushToast } from '../../state/toastStore'

const SettingsSurface = styled.div`
  display: flex; flex-direction: column; min-height: 0;
  color: #20383c; font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  .settings-header { display: flex; align-items: flex-start; gap: 14px; padding: 26px 28px 22px; border-bottom: 1px solid #e1e9e5; }
  .settings-symbol { display: grid; place-items: center; flex: 0 0 42px; height: 42px; border-radius: 12px; background: #e5efeb; color: #326e72; }
  .settings-heading { flex: 1; }
  h2 { font-size: 21px; margin: 0 0 7px; font-weight: 700; letter-spacing: -.4px; }
  .settings-heading p { margin: 0; color: #6b7d80; font-size: 12px; line-height: 1.7; }
  .MuiIconButton-root { color: #6b7d80; }
  .settings-content { padding: 24px 28px; }
  .settings-section { margin: 0 0 22px; padding: 0 0 22px; border-bottom: 1px solid #e1e9e5; }
  .settings-section:last-child { margin: 0; padding: 0; border: 0; }
  .settings-section-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; margin-bottom: 14px; }
  h3 { font-size: 14px; margin: 0; font-weight: 700; }
  .settings-section-head span { font-size: 12px; color: #6b7d80; }
  .settings-field { display: grid; gap: 7px; margin-top: 13px; font-size: 12px; color: #52696c; }
  .settings-field input { box-sizing: border-box; width: 100%; min-width: 0; height: 42px; padding: 0 12px;
    border: 1px solid #d7e2de; border-radius: 7px; background: #fff; color: #20383c; font: inherit; font-size: 13px;
    transition: border-color .15s, box-shadow .15s; }
  .settings-field input:focus { outline: none; border-color: #45888a; box-shadow: 0 0 0 3px #45888a18; }
  .settings-field input:disabled { background: #f0f4f1; color: #7a8c8a; }
  .settings-field input::placeholder { color: #94a19f; }
  .settings-key { position: relative; }
  .settings-key input { padding-right: 48px; }
  .settings-key .MuiIconButton-root { position: absolute; right: 4px; top: 3px; }
  .settings-key-label { display: flex; align-items: center; justify-content: space-between; }
  .settings-configured { color: #326e72; background: #e5efeb; border-radius: 5px; padding: 3px 8px; font-size: 11px; }
  .settings-note { margin: 9px 0 0; font-size: 11px; line-height: 1.7; color: #6b7d80; }
  .settings-status { margin: 0 0 20px; padding: 12px 14px; border-left: 3px solid #45888a; background: #edf4f0; font-size: 12px; line-height: 1.8; }
  .settings-loading { padding: 35px 0; font-size: 13px; color: #6b7d80; }
  .settings-actions { padding: 16px 28px; gap: 10px; border-top: 1px solid #e1e9e5; background: #f2f6f3; flex-wrap: wrap; }
  .settings-actions > :not(style) ~ :not(style) { margin-left: 0; }
  .MuiButton-root { text-transform: none; font: inherit; font-size: 12px; border-radius: 7px; padding: 9px 16px; color: #326e72; }
  .settings-cancel { margin-right: auto; }
  .MuiButton-outlined { border-color: #c6d8d2; background: #fff; }
  .MuiButton-contained { background: #326e72; color: #fff; box-shadow: none; }
  .MuiButton-contained:hover { background: #245b5f; box-shadow: none; }
  .MuiButton-root.Mui-disabled { color: #8a9b97; border-color: #dce5e1; }
  .MuiButton-contained.Mui-disabled { background: #dce5e1; }
  button:focus-visible { outline: 3px solid #58a0a4; outline-offset: 2px; }
  @media (max-width: 600px) {
    .settings-header { padding: 20px 18px; gap: 10px; }
    .settings-symbol { display: none; }
    .settings-content { padding: 20px 18px; }
    .settings-actions { padding: 14px 18px; gap: 6px; }
    .MuiButton-root { padding: 9px 12px; }
  }
  @media (prefers-reduced-motion: reduce) { .settings-field input { transition: none; } }
`

export default function LibrarySettings({ onClose }: { onClose: () => void }) {
  useLocale()
  const [value, setValue] = useState<Settings | null>(null)
  const [operation, setOperation] = useState<'save' | 'test' | null>(null)
  const [loadError, setLoadError] = useState('')
  const [visibleKeys, setVisibleKeys] = useState({ embedding: false, rerank: false })
  const busy = operation !== null
  const load = useCallback(async () => {
    setLoadError('')
    try { setValue(await libraryApi.settings()) }
    catch (error) { setLoadError(error instanceof Error ? error.message : String(error)) }
  }, [])
  useEffect(() => { void load() }, [load])
  const save = async (test: boolean) => {
    if (!value || busy) return
    setOperation(test ? 'test' : 'save')
    try {
      const saved = await libraryApi.saveSettings(value)
      setValue({ ...saved, embedding_api_key: '', rerank_api_key: '' })
      setVisibleKeys({ embedding: false, rerank: false })
      if (test) {
        const result = await libraryApi.test()
        pushToast(t('ui.connected_value_embedding_dimensions_reranking_available', { value1: (result.dimensions) }), 'success')
      } else {
        pushToast(saved.rebuilding ? t('ui.rebuilding_the_index_new_settings_apply_when_complete') : t('ui.library_model_settings_saved'), 'success')
        onClose()
      }
    } catch (error) { pushToast(t('ui.operation_failed_value', { value1: (error instanceof Error ? error.message : error) }), 'error') }
    finally { setOperation(null) }
  }
  const field = (key: 'embedding_model' | 'embedding_url' | 'rerank_model' | 'rerank_url', label: string) =>
    <label className="settings-field">{label}<input aria-label={label} value={value![key]} disabled={busy || value!.rebuilding}
      spellCheck={false} autoComplete="off" onChange={event => setValue({ ...value!, [key]: event.target.value })} /></label>
  const keyField = (kind: 'embedding' | 'rerank', label: string) => {
    const configured = value![`${kind}ApiKeyConfigured`]
    const visible = visibleKeys[kind]
    return <div className="settings-field">
      <label className="settings-key-label" htmlFor={`library-${kind}-key`}>
        {label} {t('ui.api_key')}{configured && <span className="settings-configured">{t('ui.configured')}</span>}
      </label>
      <div className="settings-key"><input id={`library-${kind}-key`} type={visible ? 'text' : 'password'} autoComplete="off" spellCheck={false}
        disabled={busy} value={value![`${kind}_api_key`] || ''} placeholder={configured ? t('ui.leave_blank_to_keep_the_existing_key') : t('ui.enter_the_value_model_api_key', { value1: (label) })}
        onChange={event => setValue({ ...value!, [`${kind}_api_key`]: event.target.value })} />
        <IconButton aria-label={t(visible ? 'library.key_hide' : 'library.key_show', { model: label })} aria-pressed={visible} disabled={busy} size="small"
          onClick={() => setVisibleKeys({ ...visibleKeys, [kind]: !visible })}>
          {visible ? <VisibilityOffOutlined fontSize="small" /> : <VisibilityOutlined fontSize="small" />}
        </IconButton>
      </div>
    </div>
  }

  return <Dialog open onClose={() => !busy && onClose()} maxWidth={false} aria-labelledby="library-settings-title"
    sx={{ zIndex: 23600, '& .MuiBackdrop-root': { backgroundColor: 'rgba(19,34,40,.32)', backdropFilter: 'blur(3px)' } }}
    PaperProps={{ sx: { width: 620, maxWidth: 'calc(100vw - 24px)', maxHeight: 'calc(100dvh - 32px)', m: '12px', borderRadius: '18px', bgcolor: '#fbfcfa', boxShadow: '0 24px 80px #102c3240' } }}>
    <SettingsSurface data-library-settings aria-busy={busy}>
      <header className="settings-header">
        <span className="settings-symbol"><TuneRounded /></span>
        <div className="settings-heading"><h2 id="library-settings-title">{t('ui.library_model_settings')}</h2><p>{t('ui.configure_retrieval_models_independently_of_employee_chat_models')}</p></div>
        <IconButton aria-label={t('ui.close_model_settings')} disabled={busy} onClick={onClose} size="small"><CloseRounded fontSize="small" /></IconButton>
      </header>
      <DialogContent className="settings-content">
        {value ? <>
          {value.rebuilding && <p role="status" className="settings-status">{t('ui.rebuilding_the_index_the_previous_index_remains_available_retry_failed_documents_on')}</p>}
          <section className="settings-section" aria-labelledby="library-embedding-heading">
            <div className="settings-section-head"><h3 id="library-embedding-heading">{t('ui.embedding_model')}</h3><span>{t('ui.understand_queries_and_find_relevant_documents')}</span></div>
            {field('embedding_model', t('ui.embedding_model_name'))}{field('embedding_url', t('ui.embedding_api_url'))}
            {keyField('embedding', t('ui.embedding'))}
          </section>
          <section className="settings-section" aria-labelledby="library-rerank-heading">
            <div className="settings-section-head"><h3 id="library-rerank-heading">{t('ui.reranking_model')}</h3><span>{t('ui.rank_the_most_relevant_search_results')}</span></div>
            {field('rerank_model', t('ui.reranking_model_name'))}{field('rerank_url', t('ui.reranking_api_url'))}
            {keyField('rerank', t('ui.reranking'))}
          </section>
          <p className="settings-note">{t('ui.each_model_uses_its_own_endpoint_and_key_saving_and_testing_sends')}</p>
        </> : loadError ? <div role="alert"><p>{t('ui.unable_to_load_settings')}{loadError}</p><Button onClick={() => void load()}>{t('ui.reload_settings')}</Button></div>
          : <p role="status" className="settings-loading">{t('ui.loading_settings')}</p>}
      </DialogContent>
      <DialogActions className="settings-actions">
        <Button className="settings-cancel" disabled={busy} onClick={onClose}>{t('ui.cancel')}</Button>
        <Button variant="outlined" disabled={!value || busy} onClick={() => void save(true)}>{operation === 'test' ? t('ui.testing') : t('ui.save_and_test')}</Button>
        <Button variant="contained" disabled={!value || busy} onClick={() => void save(false)}>{operation === 'save' ? t('ui.saving') : t('ui.save')}</Button>
      </DialogActions>
    </SettingsSurface>
  </Dialog>
}
