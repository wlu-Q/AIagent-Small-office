import { t, useLocale } from '../../i18n'
import React, { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { Dialog, IconButton } from '@mui/material'
import Close from '@mui/icons-material/Close'
import AccountTreeOutlined from '@mui/icons-material/AccountTreeOutlined'
import BarChartRounded from '@mui/icons-material/BarChartRounded'
import OpenInFullRounded from '@mui/icons-material/OpenInFullRounded'
import { type VisualizationLanguage } from './visualizationSpec'
import { renderVisualization } from './renderVisualization'

const Figure = styled.figure`
  margin: 18px 0; min-width: 0; max-width: 100%; overflow: hidden;
  border: 1px solid #dde8e9; border-radius: 14px; background: #fff; color: #263f4b;
  box-shadow: 0 3px 12px rgba(38, 63, 75, .035);
  .visual-toolbar { display: flex; align-items: center; gap: 10px; padding: 12px 18px;
    background: #f6faf9; border-bottom: 1px solid #e8efed; }
  .visual-kind { display: grid; place-items: center; flex: 0 0 30px; height: 30px;
    border-radius: 8px; background: #e5f1ed; color: #247f72; }
  .visual-kind svg { font-size: 18px; }
  .visual-title { flex: 1; min-width: 0; font-weight: 600; font-size: 14px; line-height: 1.5; }
  .visual-toolbar button { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;
    padding: 6px 10px; background: #fff; border: 1px solid #dce9e4; border-radius: 7px;
    color: #427165; cursor: pointer; font: inherit; font-size: 12px; line-height: 1.5; }
  .visual-toolbar button svg { font-size: 14px; }
  .visual-toolbar button:hover:not(:disabled) { background: #eaf4ef; border-color: #bad7cb; color: #216c5e; }
  .visual-toolbar button:focus-visible { outline: 2px solid #287a68; outline-offset: 2px; }
  .visual-toolbar button:disabled { opacity: .45; cursor: default; }
  .visual-image { display: block; width: 100%; max-height: 440px; object-fit: contain; padding: 24px 20px; box-sizing: border-box; }
  .visual-notice { padding: 24px 20px; color: #6d8189; font-size: 13px; }
  .visual-notice[role=alert] { color: #a34637; background: #fff9f6; }
  .visual-caption { margin: 0 20px; padding: 12px 0; border-top: 1px solid #edf2f1;
    font-size: 12px; line-height: 1.6; color: #73868c; }
  @media (max-width: 600px) {
    .visual-toolbar { padding: 10px 12px; gap: 8px; }
    .visual-image { padding: 16px 8px; }
  }
`

function VisualizationBlock({ language, source }: { language: VisualizationLanguage; source: string }) {
  const locale = useLocale()
  const figure = useRef<HTMLElement>(null)
  const [visible, setVisible] = useState(false)
  const [result, setResult] = useState<Awaited<ReturnType<typeof renderVisualization>> | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '200px' })
    if (figure.current) observer.observe(figure.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!visible) return
    let canceled = false
    setResult(null); setError('')
    renderVisualization(language, source).then(value => { if (!canceled) setResult(value) })
      .catch(cause => {
        if (!canceled) setError(cause instanceof Error ? cause.message : t('ui.check_the_chart_syntax_and_try_again'))
      })
    return () => { canceled = true }
  }, [language, source, visible, locale])
  const title = result?.title || (language === 'mermaid' ? t('ui.flow_and_architecture_diagram') : t('ui.data_chart'))
  const KindIcon = language === 'mermaid' ? AccountTreeOutlined : BarChartRounded
  return <Figure ref={figure} data-office-visualization={language} data-visual-status={error ? 'error' : result ? 'ready' : 'loading'}>
    <figcaption className="visual-toolbar"><span className="visual-kind" aria-hidden="true"><KindIcon /></span>
      <span className="visual-title">{title}</span>
      <button type="button" disabled={!result || !!error} onClick={() => setExpanded(true)} aria-label={t('ui.enlarge_chart_value', { value1: (title) })}>
        <OpenInFullRounded aria-hidden="true" />{t('ui.zoom_in')}</button>
    </figcaption>
    {error && <div className="visual-notice" role="alert">{t('ui.unable_to_display_chart')}{error.slice(0, 400)}</div>}
    {!error && (result ? <>
      <img className="visual-image" src={result.url} alt={result.description} onError={() => setError(t('ui.image_failed_to_load_reopen_the_conversation'))} />
      {result.caption && <div className="visual-caption">{result.caption}</div>}
    </> : <div className="visual-notice" role="status">{t('ui.rendering_chart')}</div>)}
    <Dialog open={expanded} onClose={() => setExpanded(false)} maxWidth={false} aria-label={title}
      sx={{ zIndex: 25000 }} PaperProps={{ ...{ 'data-office-visualization-preview': true }, sx: {
        width: 'calc(100vw - 48px)', height: 'calc(100vh - 48px)', maxHeight: 'none', borderRadius: '16px', bgcolor: '#fff', color: '#263f4b',
        border: '1px solid #dde8e9', boxShadow: '0 24px 80px rgba(25, 55, 60, .18)',
      } }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 24px', background: '#f6faf9', borderBottom: '1px solid #e5eeeb' }}>
        <KindIcon aria-hidden="true" sx={{ color: '#247f72', fontSize: 22 }} />
        <strong style={{ flex: 1, fontSize: 16 }}>{title}</strong><IconButton aria-label={t('ui.close_chart_preview')} onClick={() => setExpanded(false)} sx={{ color: '#607a79' }}><Close /></IconButton>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 32 }}>
        {result && <img src={result.url} alt={result.description} style={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain' }} />}
      </div>
      {result?.caption && <div style={{ padding: '12px 24px', fontSize: 12, color: '#73868c', borderTop: '1px solid #edf2f1' }}>{result.caption}</div>}
    </Dialog>
  </Figure>
}

export default React.memo(VisualizationBlock)
