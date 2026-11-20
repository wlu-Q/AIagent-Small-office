import { useEffect, useRef, useState, type FormEvent } from 'react'
import Dialog from '@mui/material/Dialog'
import AddRounded from '@mui/icons-material/AddRounded'
import ArrowBackRounded from '@mui/icons-material/ArrowBackRounded'
import CloseRounded from '@mui/icons-material/CloseRounded'
import HubOutlined from '@mui/icons-material/HubOutlined'
import LinkRounded from '@mui/icons-material/LinkRounded'
import TerminalRounded from '@mui/icons-material/TerminalRounded'
import SettingsOutlined from '@mui/icons-material/SettingsOutlined'
import { t, useLocale } from '../i18n'
import { readMcpRow, writeConnection, type Argument, type Connection, type McpChange, type McpService, type Pair } from '../services/office/mcp'
import { officeApi } from '../services/office/officeApi'
import './McpSettings.scss'

function PairFields({ http, rows, update }: { http: boolean; rows: Pair[]; update: (rows: Pair[]) => void }) {
  return <details className="mcp-pairs" open={rows.length > 0 || undefined}>
    <summary>{http ? t('mcp.headers') : t('mcp.env')}</summary>
    {rows.map((row, index) => <div className="mcp-pair-row" key={row.id}>
      <input aria-label={t('mcp.keyNumber', { n: index + 1 })} placeholder={http ? 'Authorization' : 'API_KEY'}
        value={row.key} required onChange={e => update(rows.map(item => item.id === row.id ? { ...row, key: e.target.value } : item))} />
      <input type="password" autoComplete="off" aria-label={t('mcp.valueNumber', { n: index + 1 })} placeholder={t('mcp.value')}
        value={row.value} required onChange={e => update(rows.map(item => item.id === row.id ? { ...row, value: e.target.value } : item))} />
      <button type="button" className="mcp-icon" aria-label={t('mcp.removeRow', { n: index + 1 })} onClick={() => update(rows.filter(item => item.id !== row.id))}><CloseRounded /></button>
    </div>)}
    <button type="button" className="mcp-text" onClick={() => update([...rows, { id: crypto.randomUUID(), key: '', value: '' }])}><AddRounded />{http ? t('mcp.addHeader') : t('mcp.addEnv')}</button>
  </details>
}

function ServiceForm({ service, services, onSave, onBack, onRemove }: {
  service?: McpService; services: McpService[]; onSave: (service: McpService) => void; onBack: () => void; onRemove: () => void
}) {
  const initial = service?.connection
  const [name, setName] = useState(service?.name ?? '')
  const [type, setType] = useState<Connection['type']>(initial?.type ?? 'http')
  const [url, setUrl] = useState(initial?.type === 'http' ? initial.url : '')
  const [headers, setHeaders] = useState<Pair[]>(initial?.type === 'http' ? initial.headers : [])
  const [command, setCommand] = useState(initial?.type === 'stdio' ? initial.command : 'npx')
  const [args, setArgs] = useState<Argument[]>(initial?.type === 'stdio' ? initial.args : [])
  const [env, setEnv] = useState<Pair[]>(initial?.type === 'stdio' ? initial.env : [])
  const [error, setError] = useState('')
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!/^[A-Za-z0-9_-]+$/.test(trimmed)) return setError(t('mcp.badName'))
    if (!service && services.some(item => item.name === trimmed)) return setError(t('mcp.duplicateName'))
    if (type === 'http' && !/^https?:\/\//i.test(url.trim())) return setError(t('mcp.badUrl'))
    const pairs = (type === 'http' ? headers : env).map(row => ({ ...row, key: row.key.trim() }))
    const keys = pairs.map(row => type === 'http' ? row.key.toLowerCase() : row.key)
    if (keys.includes('') || new Set(keys).size !== keys.length) return setError(t('mcp.badKeys'))
    const connection: Connection = type === 'http' ? { type, url: url.trim(), headers: pairs } : { type, command: command.trim(), args, env: pairs }
    onSave({ name: trimmed, enabled: service?.enabled ?? true, connection })
  }
  if (service && (service.transport === 'sse' || (initial?.type === 'stdio' && !['npx', 'uvx'].includes(initial.command)))) {
    return <div className="mcp-form">
      <div className="mcp-body"><strong>{service.name}</strong><p>{initial?.type === 'stdio'
        ? [initial.command, ...initial.args.map(arg => arg.value)].join(' ') : initial?.url}</p></div>
      <footer><button className="mcp-text mcp-remove" onClick={onRemove}>{t('mcp.delete')}</button>
        <button className="mcp-btn" onClick={onBack}>{t('mcp.back')}</button></footer>
    </div>
  }
  return <form className="mcp-form" onSubmit={submit} onChange={() => setError('')}>
    <div className="mcp-body">
      <label className="mcp-field">{t('mcp.name')}<input autoFocus={!service} readOnly={!!service} value={name} onChange={e => setName(e.target.value)} required placeholder="figma_bridge" /></label>
      <fieldset className="mcp-method"><legend>{t('mcp.method')}</legend>
        <div className="mcp-segments">{(['http', 'stdio'] as const).map(method => <label key={method}>
          <input type="radio" name="mcp-method" checked={type === method} onChange={() => setType(method)} />
          <span>{method === 'http' ? <LinkRounded /> : <TerminalRounded />}{method === 'http' ? t('mcp.http') : t('mcp.stdio')}</span>
        </label>)}</div>
      </fieldset>
      {type === 'http' ? <>
        <label className="mcp-field">{t('mcp.url')}<input type="url" value={url} onChange={e => setUrl(e.target.value)} required placeholder="https://example.com/mcp" /></label>
        <PairFields key="headers" http rows={headers} update={setHeaders} />
      </> : <>
        <label className="mcp-field">{t('mcp.command')}<select value={command} onChange={e => setCommand(e.target.value)}><option>npx</option><option>uvx</option></select></label>
        <div className="mcp-args"><strong>{t('mcp.args')}</strong>
          {args.map((arg, index) => <div className="mcp-arg-row" key={arg.id}>
            <input aria-label={t('mcp.argNumber', { n: index + 1 })} value={arg.value} required onChange={e => setArgs(args.map(item => item.id === arg.id ? { ...item, value: e.target.value } : item))} />
            <button type="button" className="mcp-icon" aria-label={t('mcp.removeRow', { n: index + 1 })} onClick={() => setArgs(args.filter(item => item.id !== arg.id))}><CloseRounded /></button>
          </div>)}
          <button type="button" className="mcp-text" onClick={() => setArgs([...args, { id: crypto.randomUUID(), value: '' }])}><AddRounded />{t('mcp.addArg')}</button>
        </div>
        <PairFields key="env" http={false} rows={env} update={setEnv} />
      </>}
      {error && <p role="alert" className="mcp-error">{error}</p>}
    </div>
    <footer>
      {service && <button type="button" className="mcp-text mcp-remove" onClick={onRemove}>{t('mcp.delete')}</button>}
      <button type="button" className="mcp-btn" onClick={onBack}>{t('ui.cancel')}</button>
      <button className="mcp-btn primary" type="submit">{service ? t('ui.save') : t('mcp.add')}</button>
    </footer>
  </form>
}

export default function McpSettings() {
  useLocale()
  const [services, setServices] = useState<McpService[]>([])
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState(false)
  const serial = useRef(0)
  const [page, setPage] = useState<'closed' | 'list' | 'form'>('closed')
  const [editing, setEditing] = useState<McpService>()
  const count = t('mcp.count', { enabled: services.filter(item => item.enabled).length, total: services.length })
  const back = () => { setEditing(undefined); setPage('list') }
  const close = () => { setEditing(undefined); setPage('closed') }
  const edit = (item?: McpService) => { setFailure(''); setEditing(item); setPage('form') }
  useEffect(() => {
    let active = true
    const request = ++serial.current
    officeApi.listMcpServices().then(rows => {
      if (active && serial.current === request) setServices(rows.map(readMcpRow))
    }).catch(error => { if (active) setFailure(String(error.message)) })
    return () => { active = false }
  }, [page])
  const connecting = services.some(service => service.status === 'connecting')
  useEffect(() => {
    if (page !== 'list' || busy || !connecting) return
    let active = true
    const request = serial.current
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const rows = await officeApi.listMcpServices()
        if (!active || request !== serial.current) return
        setServices(rows.map(readMcpRow))
        if (rows.some(row => row.status === 'connecting')) timer = setTimeout(refresh, 1000)
      } catch (error) { if (active) setFailure(error instanceof Error ? error.message : String(error)) }
    }
    timer = setTimeout(refresh, 1000)
    return () => { active = false; clearTimeout(timer) }
  }, [page, busy, connecting])
  const update = async (name: string, change: McpChange) => {
    if (busy) return
    ++serial.current
    setBusy(true); setFailure('')
    try {
      const rows = await officeApi.changeMcpService(name, change)
      setServices(rows.map(readMcpRow))
      window.dispatchEvent(new Event('office-mcp-changed'))
      back()
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error))
    } finally { setBusy(false) }
  }
  return <>
    <div className="mcp-entry">
      <span className="mcp-mark" aria-hidden="true"><HubOutlined /></span>
      <div className="mcp-entry-copy"><strong>{t('mcp.services')}</strong><small>{services.length ? count : t('mcp.noConfig')}</small></div>
      <button className="mcp-btn" type="button" aria-haspopup="dialog" onClick={back}>{t('mcp.configure')}</button>
    </div>
    <Dialog open={page !== 'closed'} onClose={busy ? undefined : close} aria-labelledby="mcp-title" maxWidth="sm" fullWidth
      sx={{ zIndex: 25000 }} PaperProps={{ className: 'mcp-panel' }}>
      <header>
        {page === 'form' ? <button className="mcp-icon" disabled={busy} aria-label={t('mcp.back')} onClick={back}><ArrowBackRounded /></button> : <span className="mcp-mark" aria-hidden="true"><HubOutlined /></span>}
        <h2 id="mcp-title">{page === 'form' ? (editing ? t('mcp.edit') : t('mcp.addTitle')) : t('mcp.services')}</h2>
        <button className="mcp-icon" disabled={busy} aria-label={t('ui.close')} onClick={close}><CloseRounded /></button>
      </header>
      {failure && <p role="alert" className="mcp-error">{failure}</p>}
      <fieldset disabled={busy} style={{ display: 'contents' }}>
      {page === 'form' ? <ServiceForm service={editing} services={services} onBack={back}
        onRemove={() => editing && void update(editing.name, { action: 'delete', revision: editing.revision })}
        onSave={next => void update(next.name, { action: editing ? 'edit' : 'create', revision: editing?.revision,
          connection: writeConnection(next.connection) })} /> : <>
        <div className="mcp-body">
          <div className="mcp-list-head"><strong>{t('mcp.configured')}</strong>{services.length > 0 && <button className="mcp-btn" onClick={() => edit()}><AddRounded />{t('mcp.add')}</button>}</div>
          {services.length ? <ul className="mcp-list">{services.map(item => <li key={item.name}>
            <span className="mcp-service-icon">{item.connection.type === 'http' ? <LinkRounded /> : <TerminalRounded />}</span>
            <div className="mcp-service-copy"><strong>{item.name}</strong><small>{item.transport === 'sse' ? 'SSE' : item.connection.type === 'http' ? t('mcp.http') : t('mcp.stdio')}</small>
              {item.enabled && <small>{item.status === 'ready' ? t('mcp.toolCount', { n: item.count ?? 0 }) : item.error || t('mcp.connecting')}</small>}</div>
            {item.enabled && <button type="button" className="mcp-text" onClick={() => void update(item.name, { action: 'reconnect', revision: item.revision })}>{t('mcp.reconnect')}</button>}
            <button className="mcp-icon" aria-label={t('mcp.editName', { name: item.name })} onClick={() => edit(item)}><SettingsOutlined /></button>
            <button type="button" role="switch" className="mcp-switch" aria-checked={item.enabled} aria-label={item.name}
              onClick={() => void update(item.name, { action: item.enabled ? 'disable' : 'enable', revision: item.revision })}><span /></button>
          </li>)}</ul> : <div className="mcp-empty">
            <span className="mcp-empty-icon"><HubOutlined /></span><strong>{t('mcp.empty')}</strong><p>{t('mcp.emptyHint')}</p>
            <button className="mcp-btn primary" onClick={() => edit()}><AddRounded />{t('mcp.add')}</button>
          </div>}
        </div>
        <footer><button className="mcp-btn" onClick={close}>{t('mcp.done')}</button></footer>
      </>}
      </fieldset>
    </Dialog>
  </>
}
