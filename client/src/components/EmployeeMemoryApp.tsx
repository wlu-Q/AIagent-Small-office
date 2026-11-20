import { useEffect, useMemo, useRef, useState } from 'react'
import { Graph, NodeEvent, type IElementEvent } from '@antv/g6'
import { useLocale } from '../i18n'
import { officeApi, type EmployeeMemoryResponse } from '../services/office/officeApi'
import { buildMemoryGraph, type MemoryGrouping } from '../workstations/memoryGraph'
import messages from '../workstations/memoryLocales.json'
import './EmployeeMemoryApp.css'

export default function EmployeeMemoryApp({ employeeId, displayName, onClose }: { employeeId: string; displayName: string; onClose: () => void }) {
  const locale = useLocale()
  const copy = messages[locale]
  const [memory, setMemory] = useState<EmployeeMemoryResponse>()
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [grouping, setGrouping] = useState<MemoryGrouping>('category')
  const [limit, setLimit] = useState(12)
  const [selectedId, setSelectedId] = useState('')
  const [detailOpen, setDetailOpen] = useState(false)
  const [graphError, setGraphError] = useState(false)
  const [ready, setReady] = useState(false)
  const graph = useRef<Graph>()
  const container = useRef<HTMLDivElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const detailDialog = useRef<HTMLDialogElement>(null)
  const categoryName = (value: string) => copy.categories[value as keyof typeof copy.categories] || value

  useEffect(() => { closeButton.current?.focus() }, [])
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      if (detailOpen) setDetailOpen(false)
      else onClose()
    }
    document.addEventListener('keydown', closeOnEscape, true)
    return () => document.removeEventListener('keydown', closeOnEscape, true)
  }, [onClose, detailOpen])
  useEffect(() => {
    let active = true
    setMemory(undefined)
    setError('')
    void officeApi.getEmployeeMemory(employeeId).then(value => { if (active) setMemory(value) }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : copy.error)
    })
    return () => { active = false }
  }, [employeeId, revision, copy.error])

  const matching = useMemo(() => (memory?.facts || []).filter(fact => (!category || fact.category === category) && fact.content.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [memory, query, category])
  const visible = useMemo(() => matching.slice(0, limit), [matching, limit])
  const selected = visible.find(fact => fact.id === selectedId)
  useEffect(() => {
    if (!detailOpen || !selected) return
    const dialog = detailDialog.current!
    dialog.showModal()
    return () => dialog.close()
  }, [detailOpen, selected])
  const categories = useMemo(() => [...new Set(memory?.facts.map(fact => fact.category))], [memory])
  const data = useMemo(() => {
    const sources = [...new Set(visible.map(fact => fact.source))]
    return buildMemoryGraph(visible, grouping, displayName, value => grouping === 'category'
      ? copy.categories[value as keyof typeof copy.categories] || value
      : value.startsWith('unattributed:') ? copy.noSource : `${copy.sourceGroup} ${sources.indexOf(value) + 1}`)
  }, [visible, grouping, displayName, copy])

  useEffect(() => {
    setReady(false)
    setGraphError(false)
    if (!container.current || !visible.length) return
    let active = true
    const panel = container.current.parentElement!
    const { width, height } = panel.getBoundingClientRect()
    const instance = new Graph({
      container: container.current, width, height, autoFit: 'view', padding: 55, animation: false, zoomRange: [0.15, 3], data,
      layout: { type: 'd3-force', iterations: 220, link: { distance: 115 }, manyBody: { strength: -550 }, collide: { radius: 62 } },
      node: {
        style: {
          size: datum => datum.data?.kind === 'employee' ? 68 : datum.data?.kind === 'group' ? 32 : 18,
          fill: datum => String(datum.data?.color), stroke: '#fff9e9', lineWidth: 3,
          labelText: datum => String(datum.data?.title), labelFill: '#3f413d', labelFontSize: 18,
          labelFontFamily: 'Microsoft YaHei, sans-serif', labelPlacement: 'bottom', labelOffsetY: 8,
          labelBackground: true, labelBackgroundFill: '#fffcf4', labelPadding: [3, 5], cursor: 'pointer',
          iconText: datum => datum.data?.kind === 'employee' ? displayName.slice(0, 1) : '', iconFill: '#fff9e9', iconFontSize: 25,
        },
        state: { selected: { stroke: '#294f4c', lineWidth: 4, halo: true, haloStroke: '#b8d7ca', haloLineWidth: 12, labelFontSize: 18 }, dim: { opacity: 0.5 } },
      },
      edge: { type: 'quadratic', style: { stroke: datum => String(datum.data?.color), lineWidth: 1.5, opacity: 0.4 }, state: { selected: { opacity: 1, lineWidth: 3 }, dim: { opacity: 0.1 } } },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'],
    })
    graph.current = instance
    const observer = new ResizeObserver(([entry]) => {
      if (!active || !instance.rendered) return
      instance.setSize(entry.contentRect.width, entry.contentRect.height)
      void instance.fitView({}, false)
    })
    observer.observe(panel)
    instance.on<IElementEvent>(NodeEvent.CLICK, event => {
      const node = instance.getNodeData(event.target.id)
      if (node.data?.kind === 'fact') { setSelectedId(String(node.data.factId)); setDetailOpen(true) }
    })
    void instance.render().then(() => { if (active) setReady(true) }).catch(() => { if (active) setGraphError(true) })
    return () => { active = false; observer.disconnect(); graph.current = undefined; instance.destroy() }
  }, [data, displayName, visible.length])

  useEffect(() => {
    if (!ready || !graph.current?.rendered) return
    const selectedNode = selected ? `fact:${selected.id}` : ''
    const edges = data.edges || []
    const parent = edges.find(edge => edge.target === selectedNode)?.source
    const states: Record<string, string[]> = {}
    for (const node of data.nodes || []) states[node.id] = !selected ? [] : node.id === selectedNode ? ['selected'] : node.id === parent || node.id === 'employee' ? [] : ['dim']
    for (const edge of edges) states[edge.id!] = !selected ? [] : edge.target === selectedNode || edge.target === parent ? ['selected'] : ['dim']
    void graph.current.setElementState(states, false)
  }, [selected, ready, data])

  const date = (value: string) => value ? new Date(value).toLocaleString(locale) : copy.noDate
  const summaries = memory ? Object.entries({ ...memory.user, ...memory.history }).filter(([, section]) => section.summary.trim()) : []
  return <section className="employee-memory-app" aria-label={copy.app} data-employee-id={employeeId}>
    <header className="memory-app-titlebar">
      <img src="assets/workstations/memory-icon.png" alt="" />
      <strong>{copy.app}</strong><span>{displayName}</span><small>{copy.readOnly}</small>
      <button ref={closeButton} onClick={onClose} aria-label={copy.back}>×</button>
    </header>
    {!memory ? <div className="memory-app-message" role="status"><p>{error ? copy.error : copy.loading}</p>{error && <><p>{error}</p><button onClick={() => setRevision(value => value + 1)}>{copy.retry}</button></>}</div> : <>
      <div className="memory-app-toolbar">
        <div className="memory-app-tabs">{(['category', 'source'] as const).map(mode => <button key={mode} aria-pressed={grouping === mode} onClick={() => setGrouping(mode)}>{copy[mode]}</button>)}</div>
        <input aria-label={copy.search} placeholder={copy.search} value={query} onChange={event => { setQuery(event.target.value); setLimit(12) }} />
        <select aria-label={copy.category} value={category} onChange={event => { setCategory(event.target.value); setLimit(12) }}><option value="">{copy.all}</option>{categories.map(value => <option key={value} value={value}>{categoryName(value)}</option>)}</select>
        <button onClick={() => setRevision(value => value + 1)}>{copy.retry}</button>
      </div>
      <div className="memory-app-body">
        <div className="memory-app-graph-panel">
          <div className="memory-app-graph" ref={container} data-graph-ready={ready} data-visible-facts={visible.length} role="img" aria-label={`${copy.app} · ${copy[grouping]}`} />
          {!visible.length && <div className="memory-app-message"><p>{memory.facts.length ? copy.noResults : copy.empty}</p>{memory.facts.length > 0 && <button onClick={() => { setQuery(''); setCategory('') }}>{copy.clear}</button>}</div>}
          {graphError && <div className="memory-app-message" role="alert">{copy.graphError}</div>}
          <div className="memory-app-legend">{copy.shown} {visible.length}{copy.of}{matching.length} {copy.facts}{visible.length < matching.length && <button onClick={() => setLimit(value => value + 12)}>{copy.more}</button>}</div>
          <div className="memory-app-controls"><button aria-label={copy.zoomOut} disabled={!ready} onClick={() => void graph.current?.zoomBy(0.8, false)}>−</button><button disabled={!ready} onClick={() => void graph.current?.fitView({}, false)}>{copy.fit}</button><button aria-label={copy.zoomIn} disabled={!ready} onClick={() => void graph.current?.zoomBy(1.25, false)}>＋</button></div>
          <p className="memory-app-gesture">{copy.controls}</p>
        </div>
        <aside className="memory-app-detail">
          <h3>{copy.list} ({visible.length})</h3>
          <p className="memory-app-note">{copy.select}</p>
          <div className="memory-app-list">{visible.map(fact => <button key={fact.id} aria-pressed={selected?.id === fact.id} onClick={() => { setSelectedId(fact.id); setDetailOpen(true) }}>{fact.content}</button>)}</div>
          {summaries.length > 0 && <details><summary>{copy.summary}</summary>{summaries.map(([key, section]) => <article key={key}><h4>{copy.sections[key as keyof typeof copy.sections] || key}</h4><p>{section.summary}</p></article>)}</details>}
        </aside>
      </div>
      <footer className="memory-app-footer"><span>{copy.updated}：{date(memory.lastUpdated)}</span></footer>
    </>}
    <dialog ref={detailDialog} className="memory-original-dialog" aria-labelledby="memory-original-title" onCancel={event => { event.preventDefault(); setDetailOpen(false) }}>
      <header><h3 id="memory-original-title">{copy.original}</h3><button aria-label={copy.closeOriginal} onClick={() => setDetailOpen(false)} autoFocus>×</button></header>
      {selected && <article className="memory-original-content">
        <span className="memory-app-category">{categoryName(selected.category)}</span>
        <p className="memory-app-original">{selected.content}</p>
        <dl><dt>{copy.recorded}</dt><dd>{date(selected.createdAt)}</dd><dt>{copy.confidence}</dt><dd>{Math.round(selected.confidence * 100)}%</dd><dt>{copy.sourceId}</dt><dd className="memory-app-source">{selected.source || copy.noSource}</dd></dl>
        {selected.sourceError && <p>{copy.priorError}：{selected.sourceError}</p>}
      </article>}
    </dialog>
  </section>
}
