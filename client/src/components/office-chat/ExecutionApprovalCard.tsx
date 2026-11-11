import { t, useLocale } from '../../i18n'
import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import TerminalOutlined from '@mui/icons-material/TerminalOutlined'
import { officeApi, type OfficeExecutionApproval } from '../../services/office/officeApi'

// One projection of server-owned approvals, shared by the composer and unread badge.
export function useExecutionApprovals(projectId: string | null | undefined) {
  const [requests, setRequests] = useState<OfficeExecutionApproval[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<{ id: string; message: string } | null>(null)
  const answered = useRef(new Set<string>())
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const result = await officeApi.listExecutionApprovals()
        if (active) setRequests(result.items.filter(item => !answered.current.has(item.approval_id)))
      } catch {
        // Keep a visible pending request on a transient read failure. Decisions
        // still reach the server and expired requests cannot be approved there.
      } finally { if (active) timer = setTimeout(refresh, 1000) }
    }
    void refresh()
    return () => { active = false; alive.current = false; clearTimeout(timer) }
  }, [])
  const decide = async (request: OfficeExecutionApproval, decision: 'approve' | 'reject') => {
    if (saving) return
    setSaving(true); setError(null)
    try {
      await officeApi.decideExecutionApproval(request.approval_id, request.request_hash, decision)
      answered.current.add(request.approval_id)
      if (alive.current) setRequests(items => items.filter(item => item.approval_id !== request.approval_id))
    } catch (err) {
      if (alive.current) setError({ id: request.approval_id, message: err instanceof Error ? err.message : t('ui.operation_failed_please_retry') })
    } finally { if (alive.current) setSaving(false) }
  }
  return { requests: requests.filter(item => projectId !== undefined && item.project_id === projectId
    && (item.conversation_kind === 'group' || (item.conversation_kind === 'private' && !!item.conversation_employee_id))), saving, error, decide }
}

const Card = styled.section`
  border: 1px solid #e2e7eb;
  border-radius: 20px;
  padding: 20px 24px 16px;
  background: #fff;
  color: #20252b;
  header { display: flex; align-items: center; gap: 9px; color: #73808c; font-size: 13px; }
  header svg { font-size: 20px; }
  h3 { margin: 14px 0; font-size: 15px; font-weight: 600; line-height: 1.7; }
  pre { margin: 12px 0; max-height: 150px; overflow: auto; white-space: pre-wrap;
    overflow-wrap: anywhere; font: 13px/1.7 Consolas, monospace; color: #647183; }
  .path { font-size: 12px; color: #7b8794; overflow-wrap: anywhere; }
  footer { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-top: 16px; }
  footer small { margin-right: auto; color: #7b8794; }
  button { border: 1px solid #e2e7eb; border-radius: 24px; padding: 9px 18px;
    background: #fff; color: #27313b; font: inherit; font-size: 14px; cursor: pointer; }
  button[data-approval-allow] { background: #20252b; border-color: #20252b; color: #fff; }
  button:hover { filter: brightness(.95); }
  button:focus-visible { outline: 2px solid #318488; outline-offset: 3px; }
  button:disabled { opacity: .55; cursor: wait; }
  [role=alert] { color: #b42318; font-size: 12px; overflow-wrap: anywhere; }
`

export default function ExecutionApprovalCard({ request, count, saving, error, onDecide }: {
  request: OfficeExecutionApproval; count: number; saving: boolean; error?: string
  onDecide: (decision: 'approve' | 'reject') => void
}) {
  useLocale()
  return <Card data-execution-approval aria-label={t('ui.operation_approval')}>
    <header><TerminalOutlined />{t('ui.terminal_and_file_operations')}<span>· {request.employee_name || request.employee_id}</span></header>
    <h3>{request.employee_name || request.employee_id} {t('ui.requests_the_following_operation_allow_it')}</h3>
    <pre data-approval-command>{request.command}</pre>
    <div className="path">{t('ui.working_directory')}{request.resolved_cwd}</div>
    {error && <p role="alert">{error}</p>}
    <footer><small>{count > 1 ? t('ui.value_more_operations_awaiting_approval_in_this_conversation', { count: (count - 1) }) : t('ui.runs_with_your_current_system_account_permissions')}</small>
      <button type="button" disabled={saving} onClick={() => onDecide('reject')}>{t('ui.reject')}</button>
      <button type="button" data-approval-allow disabled={saving} onClick={() => onDecide('approve')}>{saving ? t('ui.working') : t('ui.allow_once')}</button>
    </footer>
  </Card>
}
