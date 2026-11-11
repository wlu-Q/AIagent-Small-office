import { t, useLocale } from '../../i18n'
import React, { useEffect, useRef, useState } from 'react'
import { officeApi, OfficeWorkflow } from '../../services/office/officeApi'

const labels: Record<string, string> = {
  get proposed() { return t('ui.awaiting_confirmation') }, get confirmed() { return t('ui.pending') }, get queued() { return t('ui.queue') }, get running() { return t('ui.running') }, get succeeded() { return t('ui.execution_finished') },
  get failed() { return t('ui.failed') }, get interrupted() { return t('ui.interrupted') }, get timeout() { return t('ui.timeout') }, get passed() { return t('ui.passed') },
}

export function useWorkflow(enabled: boolean) {
  const [workflow, setWorkflow] = useState<OfficeWorkflow | null>(null)
  const [error, setError] = useState('')
  const pending = useRef<Promise<OfficeWorkflow | null> | null>(null)
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        if (!pending.current) {
          const request = officeApi.getCurrentWorkflow().finally(() => { if (pending.current === request) pending.current = null })
          pending.current = request
        }
        const next = await pending.current
        if (active) { setWorkflow(next); setError('') }
      }
      catch (e) { if (active) setError(String(e)) }
    }
    if (enabled) void refresh()
    return () => { active = false }
  }, [enabled])
  return { workflow: enabled ? workflow : null, error: enabled ? error : '' }
}

export function WorkflowPanel({ workflow, error }: { workflow: OfficeWorkflow | null; error: string }) {
  useLocale()
  const [expanded, setExpanded] = useState(false)
  if (!workflow) return error ? <div role="alert" style={{ padding: '4px 22px', fontSize: 12 }}>{t('ui.task_history_unavailable')}{error}</div> : null
  return <section data-office-history style={{ padding: '8px 22px', borderBottom: '1px solid #e2e8f0', fontSize: 12, color: '#475569', maxHeight: 190, overflowY: 'auto' }}>
    <details open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}><summary>{t('ui.task_and_validation_history')}{workflow.task_plan.length}）</summary>
      {workflow.task_plan.map(t => <div key={t.task_id} style={{ marginTop: 5 }}>{t.task} · {t.employee_id} · {labels[t.status] || t.status}</div>)}
      {Object.entries(workflow.quality).map(([id, q]) => <div key={id} style={{ marginTop: 5 }}>
        <strong>{id}：{labels[q.status] || q.status}</strong> · {q.summary}
        {q.blockingIssues.map((issue, i) => <div key={i} style={{ color: '#b45309' }}>{issue}</div>)}
        <small>{t('ui.source_run')}{q.sourceRunId} {t('ui.tool_evidence')}{q.evidence.length} {t('ui.items')}</small>
      </div>)}
    </details>
  </section>
}
