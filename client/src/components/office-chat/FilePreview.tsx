import { t, useLocale } from '../../i18n'
import React, { createContext, lazy, Suspense, useContext, useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { Dialog, IconButton, Tooltip } from '@mui/material'
import Close from '@mui/icons-material/Close'
import Fullscreen from '@mui/icons-material/Fullscreen'
import FullscreenExit from '@mui/icons-material/FullscreenExit'
import FolderOpenOutlined from '@mui/icons-material/FolderOpenOutlined'
import ContentCopyOutlined from '@mui/icons-material/ContentCopyOutlined'
import Search from '@mui/icons-material/Search'
import Refresh from '@mui/icons-material/Refresh'
import DescriptionOutlined from '@mui/icons-material/DescriptionOutlined'
import { normalizeFileReferencePath } from './fileReferencePath'
import { officeApi, type OfficeFilePreview, type OfficeFileReference } from '../../services/office/officeApi'

const CodePreview = lazy(() => import('./CodePreview'))
const MessageContent = lazy(() => import('./MessageContent'))
const PreviewContext = createContext<(file: OfficeFileReference) => void>(() => {
  useLocale()})
export const useFilePreview = () => useContext(PreviewContext)
export const fileName = (path: string) => path.split(/[/\\]/).pop() || path
export function fileSize(size: number) {
  return size < 1024 ? `${size} B` : size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} KB` : `${(size / (1024 * 1024)).toFixed(1)} MB`
}
const keyOf = (file: OfficeFileReference) => JSON.stringify([file.employeeId, file.runId, file.path])
const TabsBar = styled.header`
  display: flex; align-items: center; gap: 12px; padding: 12px 16px;
  border-bottom: 1px solid #e0e9e7; background: #f6f9f8;
  [role=tablist] { display: flex; flex: 1; min-width: 0; gap: 6px; overflow-x: auto; }
  .tab { display: inline-flex; align-items: center; flex-shrink: 0; border-radius: 9px; }
  .tab.active { background: #dcebe5; }
  button[role=tab] { display: flex; align-items: center; gap: 8px; max-width: 230px; border: 0; background: none;
    color: #29483e; padding: 10px 6px 10px 12px; font: inherit; font-size: 14px; cursor: pointer; }
  button[role=tab] span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  button:focus-visible { outline: 2px solid #247b70; outline-offset: -2px; }
`
const Toolbar = styled.div`
  display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 14px 22px; border-bottom: 1px solid #edf1f0;
  .identity { flex: 1; min-width: 150px; }
  h2 { font-size: 16px; font-weight: 650; margin: 0 0 4px; color: #253e37; }
  .path { font-size: 12px; color: #75867f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 55vw; }
  .mode { display: flex; padding: 3px; border-radius: 7px; background: #edf3f1; }
  .mode button { border: 0; border-radius: 5px; padding: 6px 12px; background: none; color: #64776f; cursor: pointer; }
  .mode button[aria-pressed=true] { background: white; color: #235f50; box-shadow: 0 1px 3px #233c371a; }
`
const Body = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; background: #fff;
  .reading { overflow: auto; flex: 1; padding: 26px max(24px, calc((100% - 850px) / 2)); }
  .image { overflow: auto; display: grid; place-items: center; flex: 1; padding: 24px; background: #f1f5f3; }
  .image img { max-width: 100%; max-height: 100%; object-fit: contain; }
`
const Notice = styled.div`
  margin: auto; padding: 32px; text-align: center; color: #65766e; font-size: 14px; line-height: 1.8;
  button { margin: 12px; padding: 8px 14px; border: 1px solid #bdcfc6; border-radius: 7px; background: #f4f8f6; color: #28594b; cursor: pointer; }
`
const Footer = styled.footer`
  display: flex; justify-content: space-between; gap: 16px; padding: 9px 22px; border-top: 1px solid #e7eeeb;
  color: #788a81; font-size: 12px; background: #fbfcfb;
`

export function FilePreviewProvider({ children }: { children: React.ReactNode }) {
  useLocale()
  const [tabs, setTabs] = useState<OfficeFileReference[]>([])
  const [active, setActive] = useState<OfficeFileReference | null>(null)
  const [open, setOpen] = useState(false)
  const [full, setFull] = useState(false)
  const [data, setData] = useState<OfficeFilePreview | null>(null)
  const [error, setError] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const [source, setSource] = useState(false)
  const [revision, setRevision] = useState(0)
  const [findRequest, setFindRequest] = useState(0)
  const [imageFailed, setImageFailed] = useState(false)
  const focusReturn = useRef<HTMLElement | null>(null)
  const activeKey = active ? keyOf(active) : ''
  const openFile = (reference: OfficeFileReference) => {
    const file = { ...reference, path: normalizeFileReferencePath(reference.path) }
    if (!open) focusReturn.current = document.activeElement as HTMLElement
    setTabs(previous => previous.some(tab => keyOf(tab) === keyOf(file)) ? previous : [...previous, file])
    setActive(file); setOpen(true); setRevision(value => value + 1)
  }
  useEffect(() => {
    if (!open || !active) return
    let canceled = false
    setLoading(true); setError(''); setActionMessage(''); setData(null); setSource(false); setImageFailed(false); setFindRequest(0)
    officeApi.previewFile(active).then(result => { if (!canceled) setData(result) })
      .catch(cause => { if (!canceled) setError(cause instanceof Error ? cause.message : String(cause)) })
      .finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [open, activeKey, revision])
  const close = () => { setOpen(false); setData(null) }
  const closeTab = (key: string) => {
    const index = tabs.findIndex(tab => keyOf(tab) === key)
    const next = tabs.filter(tab => keyOf(tab) !== key)
    setTabs(next)
    if (!next.length) { close(); setActive(null) }
    else if (activeKey === key) setActive(next[Math.min(index, next.length - 1)])
  }
  const act = async (work: () => Promise<unknown>, success = '') => {
    setActionMessage('')
    try { await work(); setActionMessage(success) }
    catch (cause) { setActionMessage(cause instanceof Error ? cause.message : String(cause)) }
  }
  const markdown = data?.language === 'markdown'
  return <PreviewContext.Provider value={openFile}>
    {children}
    <Dialog open={open} onClose={close} maxWidth={false} fullScreen={full}
      aria-labelledby="file-preview-title" disableRestoreFocus
      TransitionProps={{ onExited: () => { if (focusReturn.current?.isConnected) focusReturn.current.focus() } }}
      sx={{ zIndex: 24000 }} PaperProps={{ ...{ 'data-office-file-preview': true }, sx: {
        width: full ? '100vw' : 'min(1180px, calc(100vw - 48px))', height: full ? '100vh' : 'min(860px, calc(100vh - 48px))',
        m: full ? 0 : 2, maxHeight: 'none', borderRadius: full ? 0 : '16px', bgcolor: '#fff', color: '#253e37',
        '& .MuiIconButton-root': { color: '#60796e' }, '& .MuiIconButton-root.Mui-disabled': { color: '#b5c2bb' },
      } }}>
      <TabsBar>
        <div role="tablist" aria-label={t('ui.preview_file')} onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const index = tabs.findIndex(tab => keyOf(tab) === activeKey)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowLeft' ? -1 : 1) + tabs.length) % tabs.length
          setActive(tabs[next]); (event.currentTarget.querySelectorAll('[role=tab]')[next] as HTMLElement)?.focus()
        }}>
          {tabs.map(tab => <div className={`tab ${keyOf(tab) === activeKey ? 'active' : ''}`} key={keyOf(tab)}>
            <button role="tab" aria-selected={keyOf(tab) === activeKey} tabIndex={keyOf(tab) === activeKey ? 0 : -1}
              title={tab.path} onClick={() => setActive(tab)}><DescriptionOutlined fontSize="small" /><span>{fileName(tab.path)}</span></button>
            <IconButton size="small" aria-label={t('ui.close_file_value', { value1: (fileName(tab.path)) })} onClick={() => closeTab(keyOf(tab))}><Close sx={{ fontSize: 16 }} /></IconButton>
          </div>)}
        </div>
        <Tooltip title={full ? t('ui.restore_size') : t('ui.maximize')}><IconButton aria-label={full ? t('ui.restore_size') : t('ui.maximize_preview')} onClick={() => setFull(!full)}>{full ? <FullscreenExit /> : <Fullscreen />}</IconButton></Tooltip>
        <IconButton aria-label={t('ui.close_file_preview')} onClick={close}><Close /></IconButton>
      </TabsBar>
      <Toolbar>
        <div className="identity"><h2 id="file-preview-title">{data?.name || (active ? fileName(active.path) : t('ui.file_preview'))}</h2>
          <div className="path" title={data?.path || active?.path}>{data?.path || active?.path}</div></div>
        {markdown && <div className="mode" aria-label={t('ui.display_mode')}><button aria-pressed={!source} onClick={() => setSource(false)}>{t('ui.read')}</button><button aria-pressed={source} onClick={() => setSource(true)}>{t('ui.source')}</button></div>}
        <Tooltip title={t('ui.find_text')}><span><IconButton aria-label={t('ui.find_text_in_file')} disabled={data?.kind !== 'text'} onClick={() => { setSource(true); setFindRequest(value => value + 1) }}><Search /></IconButton></span></Tooltip>
        <Tooltip title={t('ui.copy_content')}><span><IconButton aria-label={t('ui.copy_file_content')} disabled={data?.kind !== 'text'} onClick={() => void act(() => navigator.clipboard.writeText(data?.content || ''), t('ui.content_copied'))}><ContentCopyOutlined fontSize="small" /></IconButton></span></Tooltip>
        <Tooltip title={t('ui.reload')}><IconButton aria-label={t('ui.refresh_file_preview')} disabled={loading} onClick={() => setRevision(value => value + 1)}><Refresh /></IconButton></Tooltip>
        <Tooltip title={t('ui.show_in_folder')}><IconButton aria-label={t('ui.show_in_folder')} onClick={() => active && void act(() => officeApi.revealFile(active))}><FolderOpenOutlined /></IconButton></Tooltip>
      </Toolbar>
      <Body role="tabpanel" aria-label={data?.name || t('ui.file_content')}>
        {loading ? <Notice role="status">{t('ui.reading_file')}</Notice> : error ? <Notice role="alert">{error}<br /><button onClick={() => setRevision(value => value + 1)}>{t('ui.reload')}</button></Notice> : data?.kind === 'unsupported' ? <Notice>{data.reason}<br /><button onClick={() => active && void act(() => officeApi.revealFile(active))}>{t('ui.show_in_folder')}</button></Notice> : data?.kind === 'image' ? <div className="image">{imageFailed ? <Notice role="alert">{t('ui.unable_to_decode_image_open_it_from_its_folder')}</Notice> : <img src={data.dataUrl} alt={data.name} onError={() => setImageFailed(true)} />}</div> : data?.kind === 'text' && <Suspense fallback={<Notice role="status">{t('ui.opening_preview')}</Notice>}>
          {markdown && !source ? <div className="reading"><MessageContent text={data.content || ''} employeeId={active?.employeeId} runId={active?.runId}
            basePath={active?.path || data.path} /></div> : <CodePreview key={activeKey} content={data.content || ''} language={data.language} findRequest={findRequest} />}
        </Suspense>}
      </Body>
      <Footer><span role="status">{actionMessage || t('ui.read_only_current_file_content')}</span><span>{data ? fileSize(data.size) : ''}</span></Footer>
    </Dialog>
  </PreviewContext.Provider>
}
