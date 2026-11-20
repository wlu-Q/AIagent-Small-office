import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Dialog } from '@mui/material'
import { useLocale } from '../i18n'
import { useWorkstationState } from '../workstations/workstationState'
import { createDeskLayout, handStyleForCharacter, isOperating, nextScreenClip, SCREEN_RECT, WORK_SCREEN_LOOP_MS, WORKSTATION_HEIGHT, WORKSTATION_WIDTH, type WorkstationSnapshot } from '../workstations/workstationModel'
import { drawWorkstation } from '../workstations/drawWorkstation'
import { loadWorkstationArtwork, type WorkstationArtwork } from '../workstations/workstationArtwork'
import messages from '../workstations/locales.json'
import memoryMessages from '../workstations/memoryLocales.json'
import EmployeeWorkLogsApp from './EmployeeWorkLogsApp'
import './WorkstationPreview.css'

const EmployeeMemoryApp = lazy(() => import('./EmployeeMemoryApp'))

function WorkstationContent({ snapshot }: { snapshot: WorkstationSnapshot }) {
  const locale = useLocale()
  const copy = messages[locale]
  const desktopCopy = memoryMessages[locale]
  const [activeApp, setActiveApp] = useState<'memory' | 'records' | null>(null)
  const [selectedIcon, setSelectedIcon] = useState('')
  const [pendingApp, setPendingApp] = useState(false)
  const launcher = useRef<HTMLButtonElement | null>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: WORKSTATION_WIDTH, height: WORKSTATION_HEIGHT })
  const [artwork, setArtwork] = useState<WorkstationArtwork>()
  const [artworkError, setArtworkError] = useState(false)
  const [artworkRetry, setArtworkRetry] = useState(0)
  const [motion, setMotion] = useState(false)
  const [clip, setClip] = useState(() => nextScreenClip())
  const [screenStatus, setScreenStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [retry, setRetry] = useState(0)
  const layout = useMemo(() => createDeskLayout(snapshot.workstationId, snapshot.computerFrame), [snapshot.workstationId, snapshot.computerFrame])
  const operating = isOperating(snapshot)
  const animated = operating && motion && Boolean(artwork)
  const occupied = Boolean(snapshot.employeeId)
  const desktopAvailable = occupied && snapshot.state === 'idle'
  const handStyle = occupied ? handStyleForCharacter(snapshot.characterTexture) : undefined
  const status = snapshot.state === 'working' && !snapshot.atDesk ? copy.returning : copy[snapshot.state]

  useEffect(() => {
    if (!desktopAvailable) { setActiveApp(null); setSelectedIcon(''); setPendingApp(false) }
  }, [desktopAvailable, snapshot.employeeId])

  const closeApp = () => {
    setActiveApp(null)
    requestAnimationFrame(() => launcher.current?.focus())
  }

  useEffect(() => {
    let active = true
    setArtworkError(false)
    setArtwork(undefined)
    void loadWorkstationArtwork(handStyle).then(images => {
      if (active) setArtwork(images)
    }).catch(() => { if (active) setArtworkError(true) })
    return () => { active = false }
  }, [artworkRetry, handStyle])

  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setMotion(!preference.matches && !document.hidden)
    update()
    preference.addEventListener('change', update)
    document.addEventListener('visibilitychange', update)
    return () => {
      preference.removeEventListener('change', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      const scale = Math.min(entry.contentRect.width / WORKSTATION_WIDTH, entry.contentRect.height / WORKSTATION_HEIGHT)
      setSize({ width: Math.floor(WORKSTATION_WIDTH * scale), height: Math.floor(WORKSTATION_HEIGHT * scale) })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!animated) return
    setClip(previous => nextScreenClip(previous))
    let timer: ReturnType<typeof setTimeout>
    const schedule = () => {
      timer = setTimeout(() => { setClip(previous => nextScreenClip(previous)); schedule() }, WORK_SCREEN_LOOP_MS * (Math.random() < 0.5 ? 4 : 8))
    }
    schedule()
    return () => clearTimeout(timer)
  }, [animated])

  useEffect(() => { setScreenStatus('loading') }, [clip, retry, animated])

  useEffect(() => {
    const context = canvas.current?.getContext('2d')
    if (!context || !artwork) return
    let request = 0
    let lastDraw = -Infinity
    const start = performance.now()
    const frameInterval = 1000 / 30
    const render = (now: number) => {
      const sinceDraw = now - lastDraw
      if (sinceDraw >= frameInterval) {
        drawWorkstation(context, artwork, layout, occupied, operating, animated ? now - start : 0)
        lastDraw = now - (Number.isFinite(sinceDraw) ? sinceDraw % frameInterval : 0)
      }
      if (animated) request = requestAnimationFrame(render)
    }
    render(start)
    return () => cancelAnimationFrame(request)
  }, [artwork, layout, occupied, operating, animated])

  return <>
    <header className="workstation-preview__header">
      <div>
        <h2 id="workstation-preview-title">{snapshot.displayName ? `${snapshot.displayName}${copy.title}` : copy.emptyTitle}</h2>
      </div>
      <span className={`workstation-preview__status ${operating ? 'is-working' : ''}`} role="status"><i />{status}</span>
      <button className="workstation-preview__close" aria-label={copy.close} onClick={() => useWorkstationState.getState().close()}>×</button>
    </header>
    <div className="workstation-preview__viewport" ref={viewport}>
      <div className="workstation-preview__stage" style={size} {...(activeApp ? { inert: '' } : {})} data-workstation-id={snapshot.workstationId} data-workstation-state={snapshot.state} data-operating={operating} data-artwork-ready={Boolean(artwork)} data-hand-style={handStyle}>
        <canvas ref={canvas} width={WORKSTATION_WIDTH} height={WORKSTATION_HEIGHT} role="img" aria-label={copy.scene} style={{ visibility: artwork ? 'visible' : 'hidden' }} />
        {!artwork && <div className="workstation-preview__artwork-message" role="status">
          {artworkError ? copy.artworkError : copy.loading}
          {artworkError && <button onClick={() => setArtworkRetry(value => value + 1)}>{copy.retry}</button>}
        </div>}
        {desktopAvailable && artwork && <div className="workstation-preview__desktop" aria-label={desktopCopy.app} style={{ left: `${SCREEN_RECT.x / WORKSTATION_WIDTH * 100}%`, top: `${SCREEN_RECT.y / WORKSTATION_HEIGHT * 100}%`, width: `${SCREEN_RECT.width / WORKSTATION_WIDTH * 100}%`, height: `${SCREEN_RECT.height / WORKSTATION_HEIGHT * 100}%` }}>
          {(['memory', 'records', 'recycle'] as const).map(id => <button key={id}
            className={`workstation-desktop-icon ${selectedIcon === id ? 'is-selected' : ''}`} aria-pressed={selectedIcon === id}
            aria-label={id === 'memory' ? desktopCopy.app : desktopCopy[id]} title={id === 'recycle' ? desktopCopy.pending : desktopCopy.launch}
            onClick={event => { launcher.current = event.currentTarget; setSelectedIcon(id); setPendingApp(id === 'recycle'); setActiveApp(id === 'recycle' ? null : id) }}>
            <img src={`assets/workstations/${id}-icon.png`} alt="" draggable={false} />
            <span>{id === 'memory' ? desktopCopy.app : desktopCopy[id]}</span>
          </button>)}
          {pendingApp && <span className="workstation-desktop-notice" role="status">{desktopCopy.pending}</span>}
        </div>}
        {animated && <div className="workstation-preview__screen" style={{ left: `${SCREEN_RECT.x / WORKSTATION_WIDTH * 100}%`, top: `${SCREEN_RECT.y / WORKSTATION_HEIGHT * 100}%`, width: `${SCREEN_RECT.width / WORKSTATION_WIDTH * 100}%`, height: `${SCREEN_RECT.height / WORKSTATION_HEIGHT * 100}%` }}>
          <img key={`${clip}-${retry}`} src={`assets/workstations/screens/${clip}.gif?attempt=${retry}`} alt={copy.screen} data-screen-clip={clip}
            onLoad={() => setScreenStatus('ready')} onError={() => setScreenStatus('error')} />
          {screenStatus !== 'ready' && <div className="workstation-preview__screen-message" role="status">
            {screenStatus === 'error' ? copy.screenError : copy.loading}
            {screenStatus === 'error' && <button onClick={() => setRetry(value => value + 1)}>{copy.retry}</button>}
          </div>}
        </div>}
      </div>
      {desktopAvailable && activeApp === 'records' && snapshot.employeeId && <EmployeeWorkLogsApp key={snapshot.employeeId}
        employeeId={snapshot.employeeId} displayName={snapshot.displayName || snapshot.employeeId} onClose={closeApp} />}
      {desktopAvailable && activeApp === 'memory' && snapshot.employeeId && <Suspense fallback={<div className="workstation-memory-loading" role="status">{desktopCopy.loading}</div>}>
        <EmployeeMemoryApp key={snapshot.employeeId} employeeId={snapshot.employeeId} displayName={snapshot.displayName || snapshot.employeeId}
          onClose={closeApp} />
      </Suspense>}
    </div>
    <footer className="workstation-preview__footer"><span>{copy.hint}</span></footer>
  </>
}

export default function WorkstationPreview() {
  const snapshot = useWorkstationState(state => state.active)
  return <Dialog open={Boolean(snapshot)} onClose={() => useWorkstationState.getState().close()} maxWidth={false}
    aria-labelledby="workstation-preview-title" className="workstation-preview-modal"
    PaperProps={{ className: 'workstation-preview', sx: { width: '88vw', height: '90dvh', maxWidth: 'none', maxHeight: 'none', margin: 0, borderRadius: 0 } }}>
    {snapshot && <WorkstationContent key={JSON.stringify([snapshot.workstationId, snapshot.employeeId])} snapshot={snapshot} />}
  </Dialog>
}
