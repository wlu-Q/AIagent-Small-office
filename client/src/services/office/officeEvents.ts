import {
  officeApi,
  type OfficeRunEvent,
  type OfficeRunEventsResponse,
} from './officeApi'

export async function refreshOfficeEmployeeProjection() {
  return officeApi.listEmployees({ fresh: true })
}

/**
 * Whether a bridge error means the Run no longer exists on the server.
 *
 * A Run ID routinely outlives its Run: clearing history or resetting the office
 * removes the rows while a persisted Workflow, an in-flight poll, or a stale target
 * list still holds the ID. Such a Run can never reach a terminal state, so callers
 * must treat this as "stop waiting for it" rather than as a retryable failure.
 */
export function isMissingRunError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)
  return /Run not found/i.test(message)
}

export type VisibleOfficeRunEvent = {
  runId: string
  seq: number
  kind: 'message' | 'assignment' | 'tool' | 'artifact' | 'subagent'
  label: string
  labelKey?: string
  detailKey?: string
  detail: string
  employeeId?: string
  caller?: string
  createdAt?: string
  messageId?: string
  taskId?: string
  workspaceId?: string
  hasToolCalls?: boolean
  truncated?: boolean
  callId?: string
  filePath?: string
  status?: 'running' | 'done' | 'error'
}

export type OfficeHumanInputChange = {
  type: 'requested' | 'responded'
  runId: string
  seq: number
  createdAt: string
  source: string
  requestId: string
  checkpointId?: string
  question: string
  options: Array<{ id: string; label: string; value: string }>
}

function eventObject(event: OfficeRunEvent) {
  return typeof event.content === 'string' ? undefined : event.content
}

function contentText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value.map((block) => {
      if (typeof block === 'string') return block
      if (!block || typeof block !== 'object') return ''
      const record = block as Record<string, unknown>
      if (record.hidden === true || record.hide_from_ui === true) return ''
      if (record.type && record.type !== 'text') return ''
      return typeof record.text === 'string' ? record.text : ''
    }).filter(Boolean).join('')
  }
  if (!value || typeof value !== 'object') return ''
  const record = value as Record<string, unknown>
  if (record.hidden === true || record.hide_from_ui === true || (record.type && record.type !== 'text')) return ''
  if (typeof record.text === 'string') return record.text
  if ('content' in record) return contentText(record.content)
  return ''
}

function taskCallAssignments(content: Record<string, unknown> | undefined) {
  if (!content || !Array.isArray(content.tool_calls)) return []
  return content.tool_calls.flatMap((call) => {
    if (!call || typeof call !== 'object') return []
    const record = call as Record<string, unknown>
    if (record.name !== 'task') return []
    let args: Record<string, unknown> | undefined
    if (record.args && typeof record.args === 'object') {
      args = record.args as Record<string, unknown>
    } else if (typeof record.args === 'string') {
      try {
        const parsed = JSON.parse(record.args)
        if (parsed && typeof parsed === 'object') args = parsed as Record<string, unknown>
      } catch {
        return []
      }
    }
    const employeeId = typeof args?.subagent_type === 'string' ? args.subagent_type.trim() : ''
    if (!employeeId) return []
    const description = typeof args?.description === 'string' ? args.description.trim() : ''
    return [{ employeeId, description, callId: typeof record.id === 'string' ? record.id : undefined }]
  })
}

function objectValue(value: unknown): Record<string, unknown> {
  if (typeof value === 'string') {
    try { return objectValue(JSON.parse(value)) } catch { return {} }
  }
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
}

function compact(value: unknown): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').slice(0, 240) : ''
}

// Only arguments are summarized. Tool output can contain source files, huge JSON,
// or internal protocol; it must never become conversation text.
function toolDetail(args: Record<string, unknown>) {
  if (typeof args.patch === 'string') {
    // Show touched paths in the existing activity row, never patch/file contents.
    const paths = [...args.patch.matchAll(/^\*\*\* (?:Add File|Update File|Delete File|Move to): ([^\r\n]+)$/gm)]
      .map(match => match[1])
    return compact(paths.join(' · '))
  }
  return compact(args.command || args.cmd || args.path || args.file_path || args.filename || args.query || args.pattern)
}

export function mergeVisibleRunEvents(events: VisibleOfficeRunEvent[]) {
  const merged = new Map<string, VisibleOfficeRunEvent>()
  const delegatedCalls = new Set(events.filter(event => event.kind === 'subagent' && event.taskId && event.employeeId && event.callId)
    .map(event => `${event.runId}:${event.callId}`))
  for (const event of events) {
    if (event.kind === 'tool' && event.caller !== 'lead_agent' && event.callId
      && delegatedCalls.has(`${event.runId}:${event.callId}`)) continue
    const scope = `${event.runId}:${event.taskId || ''}`
    const key = event.kind === 'message' && event.messageId ? `${scope}:${event.employeeId || ''}:message:${event.messageId}` : event.callId ? `${scope}:call:${event.callId}` : `${scope}:${event.seq}:${event.kind}:${event.employeeId || ''}`
    const previous = merged.get(key)
    merged.set(key, previous ? {
      ...previous, caller: event.caller ?? previous.caller, employeeId: event.employeeId ?? previous.employeeId,
      status: event.status, hasToolCalls: event.hasToolCalls ?? previous.hasToolCalls,
      detail: event.kind === 'message' ? event.detail : previous.detail || event.detail,
    } : event)
  }
  return [...merged.values()]
}

export function projectOfficeRunEvents(events: OfficeRunEvent[]): VisibleOfficeRunEvent[] {
  const visible: VisibleOfficeRunEvent[] = []
  const visibleToolEvents = new Set([
    'llm.tool.result',
    'tool.start',
    'tool.end',
    'tool.result',
  ])
  for (const event of events) {
    if (
      event.metadata.hidden === true
      || /hidden|thought|reasoning/i.test(event.event_type)
    ) continue

    const content = eventObject(event)
    const extra = objectValue(content?.additional_kwargs)
    if (extra.hide_from_ui === true || extra.hidden === true || content?.name === 'summary') continue
    if (event.event_type === 'llm.ai.response') {
      const caller = String(event.metadata.caller || '')
      if (/middleware|subagent/i.test(caller)) continue
      const reply = contentText(content?.content)
      if (reply.trim()) visible.push({
        runId: event.run_id, seq: event.seq, kind: 'message', label: 'AI',
        detail: reply, createdAt: event.created_at,
        messageId: typeof content?.id === 'string' ? content.id : undefined,
        hasToolCalls: Array.isArray(content?.tool_calls) && content!.tool_calls.length > 0,
      })
      for (const call of Array.isArray(content?.tool_calls) ? content!.tool_calls as unknown[] : []) {
        const record = objectValue(call)
        const name = String(record.name || '')
        if (name === 'task' || /PMResult|workflow.*result/i.test(name)) continue
        visible.push({
          runId: event.run_id, seq: event.seq, kind: 'tool', label: name, labelKey: name ? undefined : 'ui.tool_execution',
          detail: toolDetail(objectValue(record.args)), createdAt: event.created_at,
          caller: 'lead_agent',
          callId: typeof record.id === 'string' ? record.id : undefined, status: 'running',
        })
      }
      for (const assignment of taskCallAssignments(content)) {
        visible.push({
          runId: event.run_id,
          seq: event.seq,
          kind: 'assignment',
          label: '', labelKey: 'ui.assign_employee',
          detail: compact(assignment.description),
          detailKey: assignment.description ? undefined : 'ui.execute_task',
          employeeId: assignment.employeeId,
          callId: assignment.callId,
          createdAt: event.created_at,
        })
      }
      continue
    }
    if (
      event.event_type === 'subagent.start'
      || event.event_type === 'subagent.step'
      || event.event_type === 'subagent.end'
    ) {
      const employeeId = typeof content?.employee_id === 'string' ? content.employee_id.trim() : ''
      const taskId = typeof content?.task_id === 'string' ? content.task_id.trim() : ''
      if (event.event_type === 'subagent.step' && content?.kind === 'ai' && employeeId && taskId
        && event.metadata.employee_id === employeeId && event.metadata.task_id === taskId
        && typeof content.public_text === 'string' && content.public_text.trim()) {
        visible.push({
          runId: event.run_id, seq: event.seq, kind: 'message', label: 'AI',
          detail: content.public_text, employeeId, taskId, createdAt: event.created_at,
          workspaceId: typeof content.workspace_id === 'string' && content.workspace_id === event.metadata.workspace_id
            ? content.workspace_id : undefined,
          messageId: typeof content.message_id === 'string' && content.message_id
            ? content.message_id : `step:${content.message_index ?? event.seq}`,
          hasToolCalls: Array.isArray(content.tool_calls) && content.tool_calls.length > 0,
          truncated: content.public_text_truncated === true,
        })
      }
      visible.push({
        runId: event.run_id,
        seq: event.seq,
        kind: 'subagent',
        label: '', labelKey: 'ui.employee_collaboration',
        detail: compact(content?.description || content?.tool_name || content?.status || ''),
        detailKey: content?.description || content?.tool_name || content?.status ? undefined : 'ui.collaboration_in_progress',
        createdAt: event.created_at,
        employeeId: employeeId || undefined,
        taskId: taskId || undefined,
        ...(content?.kind === 'tool' ? {
          callId: typeof content.tool_call_id === 'string' ? content.tool_call_id : undefined,
          status: content.status === 'error' ? 'error' as const : 'done' as const,
        } : {}),
      })
      continue
    }
    if (visibleToolEvents.has(event.event_type)) {
      if (/PMResult|workflow.*result/i.test(String(content?.name || ''))) continue
      const caller = typeof event.metadata.caller === 'string' ? event.metadata.caller : undefined
      visible.push({
        runId: event.run_id,
        seq: event.seq,
        kind: 'tool',
        label: String(content?.name || content?.tool_name || ''),
        labelKey: content?.name || content?.tool_name ? undefined : 'ui.tool_execution',
        detail: toolDetail(objectValue(content?.args || content?.input)),
        createdAt: event.created_at,
        caller,
        employeeId: caller?.startsWith('subagent:') ? caller.slice('subagent:'.length) || undefined : undefined,
        callId: typeof content?.tool_call_id === 'string' ? content.tool_call_id : undefined,
        status: content?.status === 'error' ? 'error' : event.event_type === 'tool.start' ? 'running' : 'done',
      })
      continue
    }
    if (event.category === 'artifact' || event.event_type.includes('artifact')) {
      visible.push({
        runId: event.run_id,
        seq: event.seq,
        kind: 'artifact',
        label: '', labelKey: 'ui.generate_file',
        detail: compact(content?.path || event.content),
        filePath: typeof content?.path === 'string' ? content.path : undefined,
        createdAt: event.created_at,
      })
      continue
    }
  }
  return visible
}

export function projectOfficeHumanInputChanges(
  events: OfficeRunEvent[],
): OfficeHumanInputChange[] {
  const changes: OfficeHumanInputChange[] = []
  for (const event of events) {
    const content = eventObject(event)
    if (!content) continue
    const source = typeof content.source === 'string' ? content.source.trim() : ''
    const requestId = typeof content.request_id === 'string' ? content.request_id.trim() : ''
    const checkpointId = typeof content.checkpoint_id === 'string'
      ? content.checkpoint_id.trim()
      : typeof event.metadata.checkpoint_id === 'string'
        ? event.metadata.checkpoint_id.trim()
        : undefined
    if (!source || !requestId) continue
    if (
      event.event_type === 'runtime.human_input.requested'
      && content.kind === 'human_input_request'
      && typeof content.question === 'string'
      && content.question.trim()
    ) {
      changes.push({
        type: 'requested',
        runId: event.run_id,
        seq: event.seq,
        createdAt: event.created_at,
        source,
        requestId,
        checkpointId,
        question: content.question.trim(),
        options: Array.isArray(content.options)
          ? content.options.flatMap((option) => {
            if (!option || typeof option !== 'object') return []
            const record = option as Record<string, unknown>
            const id = typeof record.id === 'string' ? record.id.trim() : ''
            const label = typeof record.label === 'string' ? record.label.trim() : ''
            const value = typeof record.value === 'string' ? record.value : label
            return id && label && value ? [{ id, label, value }] : []
          })
          : [],
      })
      continue
    }
    if (event.event_type === 'runtime.human_input.responded') {
      changes.push({
        type: 'responded',
        runId: event.run_id,
        seq: event.seq,
        createdAt: event.created_at,
        source,
        requestId,
        checkpointId,
        question: '',
        options: [],
      })
    }
  }
  return changes
}

export async function readOfficeRunEvents(
  employeeId: string,
  runId: string,
  afterSeq?: number,
): Promise<OfficeRunEventsResponse> {
  const { events, nextAfterSeq } = await officeApi.listRunEvents(employeeId, runId, afterSeq)
  return { events, nextAfterSeq }
}
