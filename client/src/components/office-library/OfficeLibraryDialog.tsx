import { t, useLocale } from '../../i18n'
import React, { useCallback, useEffect, useRef, useState } from 'react'
import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import Button from '@mui/material/Button'
import LibrarySettings from './LibrarySettings'
import { libraryApi, LibraryDocument, libraryReference } from './libraryApi'
import IconButton from '@mui/material/IconButton'
import useMediaQuery from '@mui/material/useMediaQuery'
import CloseRounded from '@mui/icons-material/CloseRounded'
import SearchRounded from '@mui/icons-material/SearchRounded'
import FileUploadOutlined from '@mui/icons-material/FileUploadOutlined'
import AutoStoriesOutlined from '@mui/icons-material/AutoStoriesOutlined'
import ArrowBackRounded from '@mui/icons-material/ArrowBackRounded'
import DeleteOutlineRounded from '@mui/icons-material/DeleteOutlineRounded'
import { pushToast } from '../../state/toastStore'
import { LibrarySurface } from './LibraryStyles'
import DocumentContent from './DocumentContent'
import { LibraryBook, MAX_PREVIEW_BYTES, previewFileSize, previewFormat } from './libraryPreview'

const palettes = [
  ['#eeeade', '#294945', '#59958d'], ['#efeadc', '#504e3b', '#b1ae8e'],
  ['#294e50', '#f1efdc', '#719893'], ['#e9e7de', '#334e58', '#73929a'],
  ['#dbe5e7', '#30485c', '#648796'], ['#e8e8d8', '#445344', '#809984'],
  ['#3f6861', '#f4eddb', '#9bb3a0'], ['#dbe4e8', '#30495d', '#90a9ba'],
  ['#eddfd6', '#5c4d47', '#c1a195'], ['#293e52', '#eef0e8', '#7397b3'],
]

function Cover({ book }: { book: LibraryBook }) {
  useLocale()
  const [paper, ink, art] = palettes[book.color % palettes.length]
  return <div className="library-cover" style={{ '--book-paper': paper, '--book-ink': ink } as React.CSSProperties} aria-hidden="true">
    <span className="library-cover-title">{book.name}</span>
    <span className="library-cover-subtitle">{book.format}</span>
    <svg className="library-art" viewBox="0 0 160 110" preserveAspectRatio="xMidYMax slice" fill={art}>
      {book.color % 4 === 0 ? <>
        <path opacity=".45" d="M0 85 62 24 160 100V110H0Z" />
        <path opacity=".8" d="M20 110 160 4V110Z" /><path opacity=".4" d="m0 62 65 48H0Z" />
      </> : book.color % 4 === 1 ? <>
        <circle cx="132" cy="74" r="65" opacity=".5" /><circle cx="56" cy="110" r="51" opacity=".75" />
        <circle cx="132" cy="74" r="42" fill="none" stroke={ink} opacity=".15" />
      </> : book.color % 4 === 2 ? <>
        <path d="M26 110V69a17 17 0 0 1 34 0v41M72 110V46a17 17 0 0 1 34 0v64M118 110V81a17 17 0 0 1 34 0v29" opacity=".65" />
        <circle cx="89" cy="15" r="10" opacity=".8" /><circle cx="43" cy="40" r="8" opacity=".45" />
      </> : <>
        <path d="m0 76 35-20 31 15 51-47 43 29v57H0Z" opacity=".4" />
        <path d="m0 94 48-23 44 15 33-29 35 17v36H0Z" opacity=".6" />
        <path d="m0 108 67-19 43 9 50-18v30H0Z" opacity=".8" />
      </>}
    </svg>
  </div>
}

type BookPosition = { x: number; y: number; width: number; height: number }
type Motion = 'opening' | 'reading' | 'closing'

function BookTransition({ book, from, closing, reduced, onFinish }: {
  book: LibraryBook; from: BookPosition; closing: boolean; reduced: boolean; onFinish: () => void
}) {
  useLocale()
  const layer = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (reduced) { onFinish(); return }
    const host = layer.current!
    const flight = host.querySelector<HTMLElement>('.library-flight')!
    const cover = host.querySelector<HTMLElement>('.library-cover')!
    const width = Math.min(600, host.clientWidth * .72)
    const height = Math.min(640, host.clientHeight * .8)
    const x = (host.clientWidth - width) / 2
    const y = (host.clientHeight - height) / 2
    flight.style.width = `${width}px`; flight.style.height = `${height}px`
    const start = `translate(${from.x}px, ${from.y}px) scale(${from.width / width}, ${from.height / height})`
    const end = `translate(${x}px, ${y}px) scale(1)`
    const options: KeyframeAnimationOptions = { duration: closing ? 520 : 720, easing: 'linear', fill: 'both', direction: closing ? 'reverse' : 'normal' }
    const move = flight.animate([
      { transform: start, opacity: 1, offset: 0, easing: 'cubic-bezier(.22,.7,.18,1)' },
      { transform: end, opacity: 1, offset: .6 },
      { transform: end, opacity: 1, offset: .86 },
      { transform: end, opacity: 0, offset: 1 },
    ], options)
    const flip = cover.animate([
      { transform: 'rotateY(0deg)', offset: 0 },
      { transform: 'rotateY(-12deg)', offset: .3, easing: 'ease-in-out' },
      { transform: 'rotateY(-152deg)', offset: .87 },
      { transform: 'rotateY(-165deg)', offset: 1 },
    ], options)
    let cancelled = false
    void move.finished.then(() => { if (!cancelled) onFinish() }).catch(() => {})
    return () => { cancelled = true; move.cancel(); flip.cancel() }
  }, [book, from, closing, reduced, onFinish])
  return <div className="library-transition" ref={layer} aria-hidden="true" data-book-transition={closing ? 'closing' : 'opening'}>
    <div className="library-flight"><div className="library-leaf"><span>{book.name}</span><i /><i /><i /><i /><i /></div><Cover book={book} /></div>
  </div>
}

function Reader({ book, motion, onBack, onClose }: { book: LibraryBook; motion: Motion; onBack: () => void; onClose: () => void }) {
  useLocale()
  const ready = motion === 'reading'
  const title = useRef<HTMLHeadingElement>(null)
  useEffect(() => { if (ready) title.current?.focus() }, [ready])
  return <div className={`library-reader ${ready ? 'is-ready' : `is-${motion}`}`} aria-hidden={!ready}>
    <header className="library-reader-toolbar">
      <button className="library-back" type="button" disabled={!ready} onClick={onBack}><ArrowBackRounded fontSize="small" />{t('ui.back_to_shelf')}</button>
      <h2 id="office-library-title" tabIndex={-1} ref={title}>{book.file.name}</h2>
      <IconButton aria-label={t('ui.close_library')} onClick={onClose} disabled={!ready}><CloseRounded /></IconButton>
    </header>
    <div className="library-reader-layout"><DocumentContent book={book} /></div>
  </div>
}

export default function OfficeLibraryDialog({ open, onClose, source }: { open: boolean; onClose: () => void; source?: string }) {
  useLocale()
  const [query, setQuery] = useState('')
  const [documents, setDocuments] = useState<LibraryDocument[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [confirmation, setConfirmation] = useState<{ title: string; message: string } | null>(null)
  const confirmResolve = useRef<((value: boolean) => void) | null>(null)
  const reloadSequence = useRef(0)
  const appliedSequence = useRef(0)
  const confirm = (title: string, message: string) => new Promise<boolean>(resolve => { confirmResolve.current = resolve; setConfirmation({ title, message }) })
  const answer = (yes: boolean) => { confirmResolve.current?.(yes); confirmResolve.current = null; setConfirmation(null) }
  const reload = useCallback(async () => {
    const sequence = ++reloadSequence.current
    const result = await libraryApi.list()
    if (sequence < appliedSequence.current) return
    appliedSequence.current = sequence
    setDocuments(result.documents)
    setBooks(result.documents.map((doc, color) => ({ id: doc.id, name: doc.display_name,
      format: previewFormat(doc.filename)!, color, file: new File([], doc.filename), size: doc.size })))
    setError('')
  }, [])
  useEffect(() => {
    if (!open) return
    let closed = false; let timer: ReturnType<typeof setTimeout>
    const poll = async () => { try { await reload() } catch (cause) { if (!closed) setError(cause instanceof Error ? cause.message : String(cause)) }
      if (!closed) timer = setTimeout(poll, 2500) }
    void poll()
    return () => { closed = true; clearTimeout(timer) }
  }, [open, reload])
  useEffect(() => () => { confirmResolve.current?.(false) }, [])

  const [books, setBooks] = useState<LibraryBook[]>([])
  const [active, setActive] = useState<LibraryBook | null>(null)
  const [motion, setMotion] = useState<Motion>('reading')
  const [origin, setOrigin] = useState<BookPosition>({ x: 0, y: 0, width: 1, height: 1 })
  const picker = useRef<HTMLInputElement>(null)
  const surface = useRef<HTMLDivElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const returnBook = useRef<string | null>(null)
  const shelfScroll = useRef(0)
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)')
  const transitionDone = useCallback(() => {
    if (motion === 'closing') setActive(null)
    setMotion('reading')
  }, [motion])
  useEffect(() => {
    if (!open || active) return
    if (scroll.current) scroll.current.scrollTop = shelfScroll.current
    if (returnBook.current) {
      const button = Array.from(surface.current?.querySelectorAll<HTMLButtonElement>('[data-library-book]') || [])
        .find(item => item.dataset.libraryBook === returnBook.current)
      button?.focus({ preventScroll: true }); returnBook.current = null
    }
  }, [open, active])
  const viewBook = async (book: LibraryBook, button: HTMLButtonElement) => {
    if (active || busy) return
    const cover = button.querySelector<HTMLElement>('.library-cover')!
    // Use the resting box rather than the pointer-tilted bounding rectangle.
    const rect = cover.parentElement!.getBoundingClientRect()
    const bounds = surface.current!.getBoundingClientRect()
    setOrigin({ x: rect.left - bounds.left + (rect.width - cover.offsetWidth) / 2,
      y: rect.bottom - bounds.top - 8 - cover.offsetHeight, width: cover.offsetWidth, height: cover.offsetHeight })
    shelfScroll.current = scroll.current?.scrollTop || 0; returnBook.current = book.id
    setBusy(true)
    try { const file = await libraryApi.file(book.id); setMotion('opening'); setActive({ ...book, file }) }
    catch (cause) { pushToast(t('ui.unable_to_read_value', { value1: (cause instanceof Error ? cause.message : cause) }), 'error') }
    finally { setBusy(false) }
  }
  const back = () => { if (motion === 'reading') setMotion('closing') }
  const tilt = (event: React.PointerEvent<HTMLButtonElement>) => {
    if (reduced || active || event.pointerType === 'touch') return
    const button = event.currentTarget
    const rect = button.querySelector('.library-book-space')!.getBoundingClientRect()
    const x = Math.max(-1, Math.min(1, (event.clientX - rect.left) / rect.width * 2 - 1))
    const y = Math.max(-1, Math.min(1, (event.clientY - rect.top) / rect.height * 2 - 1))
    // Positive X rotation pushes the top away; positive Y pushes the right away.
    button.style.setProperty('--tilt-x', `${-y * 5}deg`)
    button.style.setProperty('--tilt-y', `${x * 5}deg`)
  }
  const resetTilt = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.currentTarget.style.removeProperty('--tilt-x'); event.currentTarget.style.removeProperty('--tilt-y')
  }
  const selectFiles = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []); event.target.value = ''
    setBusy(true)
    try {
      for (const file of files) {
        if (!previewFormat(file.name)) throw new Error(t('ui.only_txt_markdown_pdf_and_docx_are_supported'))
        if (file.size > MAX_PREVIEW_BYTES) throw new Error(t('ui.files_must_not_exceed_20_mb'))
        const result = await libraryApi.upload(file)
        if (result.conflict) {
          if (!await confirm(t('ui.file_already_exists'), t('ui.a_document_named_value_with_the_same_type_already_exists_replace_and', { value1: (result.conflict.filename) }))) continue
          const again = await libraryApi.upload(file, result.conflict as LibraryDocument)
          if (again.conflict) throw new Error(t('ui.document_version_changed_upload_again_to_confirm'))
        }
        pushToast(t('ui.value_uploaded_processing', { value1: (file.name) }), 'success')
        await reload()
      }
      setQuery('')
    } catch (cause) { pushToast(t('ui.upload_failed_value', { value1: (cause instanceof Error ? cause.message : cause) }), 'error') }
    finally { setBusy(false) }
  }
  const operate = async (operation: () => Promise<unknown>) => {
    setBusy(true)
    try { await operation(); await reload() } catch (cause) { pushToast(t('ui.operation_failed_value', { value1: (cause instanceof Error ? cause.message : cause) }), 'error') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    const reference = source && libraryReference(source)
    if (!open || !reference) return
    let cancelled = false
    void libraryApi.file(reference.id, reference.version, reference.part).then(file => {
      if (!cancelled) { setActive({ id: reference.id, file, format: previewFormat(file.name)!, name: file.name, color: 0, part: reference.part, location: file.location, quote: file.quote }); setMotion('reading') }
    }).catch(cause => { if (!cancelled) pushToast(t('ui.unable_to_read_source_value', { value1: (cause instanceof Error ? cause.message : cause) }), 'error') })
    return () => { cancelled = true }
  }, [open, source])
  const filtered = books.filter(book => `${book.name} ${book.file.name}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  return <Dialog open={open} onClose={(_, reason) => {
    if (busy) return
    if (reason === 'escapeKeyDown' && active) back()
    else onClose()
  }} maxWidth={false} aria-labelledby="office-library-title" disableRestoreFocus
    transitionDuration={reduced ? 0 : 180} TransitionProps={{ onExited: () => {
      setActive(null); setMotion('reading'); returnBook.current = null
      document.querySelector<HTMLButtonElement>('[data-office-library-launcher]')?.focus()
    } }} sx={{ zIndex: 23500, '& .MuiBackdrop-root': { backgroundColor: 'rgba(19, 34, 40, .4)', backdropFilter: 'blur(5px)' } }}
    PaperProps={{ ...{ 'data-office-library': true }, sx: {
      width: 'min(1130px, calc(100vw - 40px))', height: 'min(790px, calc(100dvh - 56px))',
      maxHeight: 'calc(100dvh - 32px)', m: 2, borderRadius: '18px', bgcolor: '#fbfcfa',
      boxShadow: '0 30px 90px #102c3240', overflow: 'hidden',
      '@media (max-width: 600px)': { width: 'calc(100vw - 20px)', height: 'calc(100dvh - 24px)', m: '10px' },
    } }}>
    <LibrarySurface ref={surface}>
      <div className={`library-shelf ${active && motion === 'reading' ? 'is-hidden' : ''} ${active ? 'is-inactive' : ''}`} aria-hidden={Boolean(active)}
        {...(active ? { inert: '' } : {})}>
        <header className="library-header">
          <div className="library-heading"><h2 id={active ? undefined : 'office-library-title'}>{t('ui.office_library')}</h2><p>{t('ui.keep_your_references_close_at_hand')}</p></div>
          <div className="library-tools">
            <label className="library-search"><SearchRounded fontSize="small" /><input aria-label={t('ui.search_document_names')} placeholder={t('ui.search_document_names')} value={query} onChange={event => setQuery(event.target.value)} /></label>
            <button className="library-primary" type="button" disabled={busy} onClick={() => picker.current?.click()}><FileUploadOutlined fontSize="small" />{t('ui.upload_documents')}</button>
          </div>
          <button className="library-text-button" disabled={busy} onClick={() => setSettingsOpen(true)}>{t('ui.model_settings')}</button>
          <IconButton className="library-close" aria-label={t('ui.close_library')} disabled={busy} onClick={onClose}><CloseRounded fontSize="small" /></IconButton>
        </header>
        <div className="library-scroll" ref={scroll}>
          {error && <p role="alert">{error}</p>}
          {filtered.length ? <div className="library-grid">
            {filtered.map(book => <div key={book.id}><button type="button" className={`library-book ${active?.id === book.id ? 'is-opening' : ''}`} data-library-book={book.id}
              aria-label={t('ui.read_value', { value1: (book.name) })} title={book.file.name} onClick={event => viewBook(book, event.currentTarget)}
              onPointerMove={tilt} onPointerLeave={resetTilt} onPointerCancel={resetTilt}>
              <div className="library-book-space"><Cover book={book} /></div>
              <span className="library-book-name">{book.name}</span>
            </button>
              <div className="library-book-meta">
                <span className="library-format">{book.format}</span><span>{previewFileSize(book.size || book.file.size)}</span>
                <button className="library-delete" type="button" aria-label={t('ui.delete_value', { value1: (book.name) })} disabled={busy} onClick={async () => { if (await confirm(t('ui.delete_document'), t('ui.delete_value_it_will_no_longer_be_searchable_or_readable', { value1: (book.name) }))) void operate(() => libraryApi.remove(book.id)) }}>{t('ui.delete')}</button>
              </div>
              <div className="library-item-status" title={documents.find(doc => doc.id === book.id)?.error}>
                {({staged:t('ui.waiting_for_index_switch'),queued:t('ui.waiting_to_process'),processing:t('ui.processing'),ready:t('ui.searchable'),failed:t('ui.processing_failed')} as Record<string,string>)[documents.find(doc => doc.id === book.id)?.status || '']}
                {documents.find(doc => doc.id === book.id)?.error && <span role="alert"> · {documents.find(doc => doc.id === book.id)?.error}</span>}
                {documents.find(doc => doc.id === book.id)?.pending && documents.find(doc => doc.id === book.id)?.active && <span> {t('ui.previous_version_remains_searchable')}</span>}
              </div>
              <div className="library-item-actions">
                {documents.find(doc => doc.id === book.id)?.status === 'failed' && <button disabled={busy} onClick={() => void operate(() => libraryApi.retry(book.id))}>{t('ui.retry')}</button>}
              </div>
            </div>)}
          </div> : <div className="library-empty"><AutoStoriesOutlined />
            <h3>{query.trim() ? t('ui.no_matching_documents') : t('ui.build_your_reference_shelf')}</h3>
            <p>{query.trim() ? t('ui.try_another_name_or_clear_the_search_to_see_all_documents') : t('ui.first_configure_embedding_and_reranking_model_keys_in_model_settings_then_upload')}</p>
            {query.trim() ? <button className="library-text-button" type="button" onClick={() => setQuery('')}>{t('ui.clear_search')}</button>
              : <button className="library-primary" type="button" disabled={busy} onClick={() => picker.current?.click()}><FileUploadOutlined fontSize="small" />{t('ui.upload_documents')}</button>}
          </div>}
        </div>
        <footer className="library-footer"><span aria-live="polite">{query.trim() ? t('ui.value_matching_documents', { count: (filtered.length) }) : t('ui.value_documents_in_total', { count: (books.length) })}</span></footer>
      </div>
      {active && <Reader book={active} motion={motion} onBack={back} onClose={onClose} />}
      {active && motion !== 'reading' && <BookTransition book={active} from={origin} closing={motion === 'closing'} reduced={reduced} onFinish={transitionDone} />}
      <input ref={picker} type="file" accept=".txt,.md,.markdown,.pdf,.docx" multiple hidden onChange={selectFiles} />
      {settingsOpen && <LibrarySettings onClose={() => setSettingsOpen(false)} />}
      <Dialog open={Boolean(confirmation)} onClose={() => answer(false)} maxWidth={false}
        aria-labelledby="library-confirm-title" aria-describedby="library-confirm-description"
        sx={{ zIndex: 23700, '& .MuiBackdrop-root': { backgroundColor: 'rgba(19,34,40,.32)', backdropFilter: 'blur(3px)' } }}
        PaperProps={{ sx: {
          width: 440, maxWidth: 'calc(100vw - 32px)', m: 2, borderRadius: '18px', bgcolor: '#fbfcfa',
          color: '#20383c', boxShadow: '0 24px 80px #102c3240', fontFamily: "'Microsoft YaHei', 'PingFang SC', sans-serif",
          '& .MuiButton-root': { fontFamily: 'inherit', fontSize: 13, borderRadius: '8px', px: 2.5, py: 1, textTransform: 'none', boxShadow: 'none' },
          '& button:focus-visible': { outline: '3px solid #58a0a4', outlineOffset: '3px' },
        } }}>
        <DialogTitle id="library-confirm-title" sx={{ display: 'flex', alignItems: 'center', gap: '12px', p: '26px 28px 16px', fontFamily: 'inherit', fontSize: 20, fontWeight: 700 }}>
          <span style={{ display: 'grid', placeItems: 'center', width: 42, height: 42, borderRadius: 12, flexShrink: 0,
            background: confirmation?.title === t('ui.file_already_exists') ? '#e5efeb' : '#f6e9e5', color: confirmation?.title === t('ui.file_already_exists') ? '#326e72' : '#a34c42' }}>
            {confirmation?.title === t('ui.file_already_exists') ? <FileUploadOutlined /> : <DeleteOutlineRounded />}
          </span>
          {confirmation?.title}
          <IconButton aria-label={t('ui.close_confirmation')} size="small" onClick={() => answer(false)} sx={{ ml: 'auto', mr: '-8px', color: '#6b7d80' }}><CloseRounded fontSize="small" /></IconButton>
        </DialogTitle>
        <DialogContent sx={{ px: '28px', pb: '26px' }}>
          <p id="library-confirm-description" style={{ margin: 0, fontSize: 14, lineHeight: 1.9, color: '#52696c', overflowWrap: 'anywhere' }}>{confirmation?.message}</p>
        </DialogContent>
        <DialogActions sx={{ px: '28px', py: '18px', gap: '10px', borderTop: '1px solid #e1e9e5', bgcolor: '#f2f6f3', '& > :not(style) ~ :not(style)': { ml: 0 } }}>
          <Button autoFocus onClick={() => answer(false)} variant="outlined" sx={{ borderColor: '#c6d8d2', color: '#326e72', bgcolor: '#fff', '&:hover': { borderColor: '#45888a', bgcolor: '#edf4f0' } }}>{t('ui.cancel')}</Button>
          <Button onClick={() => answer(true)} variant="contained" sx={{ bgcolor: confirmation?.title === t('ui.file_already_exists') ? '#326e72' : '#a34c42', '&:hover': { bgcolor: confirmation?.title === t('ui.file_already_exists') ? '#245b5f' : '#893e35', boxShadow: 'none' } }}>{confirmation?.title === t('ui.file_already_exists') ? t('ui.replace') : t('ui.confirm_deletion')}</Button>
        </DialogActions>
      </Dialog>
    </LibrarySurface>
  </Dialog>
}
