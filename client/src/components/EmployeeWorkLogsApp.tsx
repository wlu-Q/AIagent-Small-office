import { useEffect, useRef, useState } from 'react'
import { useLocale } from '../i18n'
import { officeApi, type WorkLogDay, type WorkLogDays } from '../services/office/officeApi'
import messages from '../workstations/workLogLocales.json'
import './EmployeeWorkLogsApp.css'

type Copy = typeof messages['zh-CN']

function Journal({ employeeId, day, copy, locale }: { employeeId: string; day: string; copy: Copy; locale: string }) {
  const [log, setLog] = useState<WorkLogDay>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let live = true
    setError('')
    setLog(undefined)
    if (!day) return
    officeApi.getWorkLogDay(employeeId, day).then(value => { if (live) setLog(value) })
      .catch(reason => { if (live) setError(String(reason)) })
    return () => { live = false }
  }, [employeeId, day, attempt])
  const stamp = (value: string) => new Date(value).toLocaleString(locale, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  if (error) return <p role="alert">{copy.error} · {error} <button onClick={() => setAttempt(value => value + 1)}>{copy.retry}</button></p>
  if (!log) return <p role="status">{copy.loading}</p>
  return <article className="work-journal">
    <h3>{copy.today}</h3>
    {!log.tasks.length && <p>{copy.empty}</p>}
    {log.tasks.map((task, index) => {
      const problem = task.outcome === 'timeout' ? copy.timeout : task.outcome === 'interrupted' ? copy.interrupted
        : ['error', 'failed'].includes(task.outcome) ? copy.failed : null
      const failed = task.steps.filter(step => step.error).map(step => step.text)
      const issues = [problem, task.issue, ...failed.map(tool => tool + ' · ' + copy.toolError)].filter(Boolean)
      return <section key={task.id}>
        <small>{copy[task.source]}</small>
        <h4>{index + 1}. {task.task || copy.untitled}</h4>
        <dl>
          <dt>{copy.time}</dt><dd>{task.startedAt || task.endedAt
            ? [task.startedAt ? stamp(task.startedAt) : copy.missing, task.endedAt ? stamp(task.endedAt) : copy.missing].join(' — ') : copy.missing}
            {!task.endedAt && <div>{copy.recorded} {stamp(task.recordedAt)}</div>}</dd>
          <dt>{copy.steps}</dt><dd>{task.steps.length ? <ol>{task.steps.map((step, position) =>
            <li key={position}>{step.text}{step.error && ' · ' + copy.toolError}</li>)}</ol> : copy.missing}</dd>
          <dt>{copy.result}</dt><dd>{task.result || copy.missing}</dd>
          <dt>{copy.issue}</dt><dd>{issues.length ? issues.join('\n') : copy.none}</dd>
        </dl>
      </section>
    })}
  </article>
}

export default function EmployeeWorkLogsApp({ employeeId, displayName, onClose }: { employeeId: string; displayName: string; onClose: () => void }) {
  const locale = useLocale()
  const copy = messages[locale]
  const [days, setDays] = useState<WorkLogDays['days']>([])
  const [before, setBefore] = useState<string>()
  const [next, setNext] = useState<string | null>(null)
  const [selected, setSelected] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const file = useRef<HTMLButtonElement | null>(null)
  const back = useRef<HTMLButtonElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const title = (day: string) => new Intl.DateTimeFormat(locale, { dateStyle: 'long', timeZone: 'UTC' })
    .format(new Date(day + 'T00:00:00Z')) + (locale === 'en' ? ' · ' : '') + copy.journal
  const toFolder = () => { setSelected(''); requestAnimationFrame(() => file.current?.focus()) }
  useEffect(() => { close.current?.focus() }, [])
  useEffect(() => { if (selected) back.current?.focus() }, [selected])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      if (selected) toFolder()
      else onClose()
    }
    document.addEventListener('keydown', escape, true)
    return () => document.removeEventListener('keydown', escape, true)
  }, [selected, onClose])
  useEffect(() => {
    let live = true
    setLoading(true)
    setError('')
    officeApi.listWorkLogDays(employeeId, before).then(page => {
      if (!live) return
      setDays(previous => before ? [...previous, ...page.days] : page.days)
      setNext(page.nextBefore)
    }).catch(reason => { if (live) setError(String(reason)) }).finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [employeeId, before, attempt])
  return <section className="work-log-app" aria-label={copy.folder}>
    <header><img src="assets/workstations/records-icon.png" alt="" /><strong>{copy.folder}</strong><span>{displayName}</span>
      <button ref={close} aria-label={copy.desktop} onClick={onClose}>×</button></header>
    <nav aria-label={copy.folder}>
      {selected && <button ref={back} aria-label={copy.back} onClick={toFolder}>←</button>}
      {displayName} <b>›</b> {copy.folder}{selected && <> <b>›</b> {title(selected)}</>}
    </nav>
    <div className="work-log-files" hidden={Boolean(selected)}>
      <div className="work-log-columns">{copy.name}</div>
      {days.map(day => <button className="work-log-file" key={day.date} onClick={event => {
        file.current = event.currentTarget; setSelected(day.date)
      }}><i aria-hidden="true">≡</i>{title(day.date)}</button>)}
      {loading && <p role="status">{copy.loading}</p>}
      {error && <p role="alert">{copy.error} · {error} <button onClick={() => setAttempt(value => value + 1)}>{copy.retry}</button></p>}
      {!loading && !error && !days.length && <p>{copy.empty}</p>}
      {!loading && !error && next && <button className="work-log-more" onClick={() => setBefore(next)}>{copy.more}</button>}
    </div>
    <div className="work-log-reader" hidden={!selected}><Journal employeeId={employeeId} day={selected} locale={locale} copy={copy} /></div>
  </section>
}
