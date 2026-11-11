import { t, i18n } from '../../i18n'
import type { OfficeHumanInputChange, VisibleOfficeRunEvent } from '../../services/office/officeEvents'
import type { OfficeConversationMessage, OfficeRun, OfficeEmployeeSummary, OfficeGroupMessage } from '../../services/office/officeApi'

// A checkpoint is a model-context window, not a deletion notice for the UI.
// This is session-local; rebinding/unmounting still discards the entire cache.
export function mergeConversationMessages(previous: OfficeConversationMessage[], incoming: OfficeConversationMessage[]) {
  const messages = new Map(previous.map(message => [`${message.role}:${message.messageId}`, message]))
  for (const message of incoming) {
    const key = `${message.role}:${message.messageId}`
    const existing = messages.get(key)
    // A transient checkpoint can briefly contain an empty or shorter streamed
    // value while a newer turn is being persisted. Never regress visible text;
    // merge missing identity metadata from whichever copy has it.
    const incomingIsShorterPrefix = Boolean(
      existing?.content.trim()
      && message.content.trim()
      && message.content.length < existing.content.length
      && existing.content.startsWith(message.content),
    )
    if (existing && (incomingIsShorterPrefix || !message.content.trim())) {
      messages.set(key, {
        ...existing,
        runId: existing.runId || message.runId,
        createdAt: existing.createdAt || message.createdAt,
      })
    } else {
      messages.set(key, {
        ...message,
        runId: message.runId || existing?.runId || null,
        createdAt: message.createdAt || existing?.createdAt || null,
      })
    }
  }
  return [...messages.values()].sort((left, right) => {
    const leftTime = left.createdAt ? Date.parse(left.createdAt) : Number.NaN
    const rightTime = right.createdAt ? Date.parse(right.createdAt) : Number.NaN

    // The checkpoint order is authoritative when either message has no valid
    // timestamp. Sorting an absent timestamp as an empty string moved assistant
    // replies ahead of the user's message in private conversations.
    if (!Number.isFinite(leftTime) || !Number.isFinite(rightTime)) return 0

    return leftTime - rightTime || left.messageId.localeCompare(right.messageId)
  })
}

export function groupActivities<T extends { key: string; activity?: { runId: string } }>(items: T[]) {
  const groups: T[][] = []
  for (const item of items) {
    const previous = groups[groups.length - 1]
    if (item.activity && previous?.[0].activity?.runId === item.activity.runId) previous.push(item)
    else groups.push([item])
  }
  return groups
}

export function workProgressGroups(runs: OfficeRun[], waitingRunIds: Set<string>) {
  const groups = new Map<string, OfficeRun[]>()
  const byId = new Map(runs.map(run => [run.runId, run]))
  for (const run of runs) {
    let root = run
    const visited = new Set<string>()
    while (root.resumeSourceRunId && byId.has(root.resumeSourceRunId) && !visited.has(root.runId)) {
      visited.add(root.runId)
      root = byId.get(root.resumeSourceRunId)!
    }
    const key = `${run.employeeId}:${root.workId || root.runId}`
    groups.set(key, [...(groups.get(key) || []), run])
  }
  return [...groups].map(([key, members]) => {
    members.sort((left, right) => left.createdAt.localeCompare(right.createdAt))
    const latest = members[members.length - 1]
    const waiting = waitingRunIds.has(latest.runId)
    const active = waiting || ['pending', 'running'].includes(latest.status) || latest.finalizing
    const starts = members.map(run => Date.parse(run.workStartedAt || run.createdAt)).filter(Number.isFinite)
    return { key, runs: members, latest, active, waiting,
      startedAt: starts.length ? Math.min(...starts) : undefined,
      finishedAt: latest.runtimeFinishedAt ? Date.parse(latest.runtimeFinishedAt) : undefined }
  })
}

export function formatWorkDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return [[hours, 'hour'], [minutes, 'minute'], [seconds % 60, 'second']].filter(([value, unit]) => value || unit === 'second').map(([value, unit]) => new Intl.NumberFormat(i18n.language, { style: 'unit', unit: String(unit), unitDisplay: 'short' }).format(Number(value))).join(' ')
}

export function isSavedRunMessage(event: VisibleOfficeRunEvent,
  messages: Array<{ id: string; role: string; text: string; runId?: string | null; employeeId?: string }>,
  events: VisibleOfficeRunEvent[]) {
  if (event.kind !== 'message' || event.taskId) return false
  return messages.some(message => {
    if (message.role !== 'assistant' || (event.employeeId && message.employeeId !== event.employeeId)
      || (message.runId && message.runId !== event.runId)) return false
    if (event.messageId && message.id === event.messageId) return true
    return message.runId === event.runId && event.hasToolCalls === false
      && message.text.trim() === event.detail.trim()
      && !events.some(next => next.runId === event.runId && !next.taskId && next.kind === 'message' && next.seq > event.seq)
  })
}

export function runEventAnchor(event: VisibleOfficeRunEvent,
  messages: Array<{ id: string; role: string; runId?: string | null; time: number }>,
  events: VisibleOfficeRunEvent[]) {
  let previous = -1
  let next = -1
  const final = messages.findIndex(message => message.role === 'assistant' && message.runId === event.runId)
  messages.forEach((message, index) => {
    if (message.role !== 'assistant' || message.runId !== event.runId) return
    const reply = events.find(candidate => candidate.runId === event.runId && !candidate.taskId
      && candidate.kind === 'message' && candidate.messageId === message.id)
    if (!reply) return
    if (reply.seq <= event.seq) previous = index
    else if (next < 0) next = index
  })
  const eventTime = Date.parse(event.createdAt || '')
  const byTime = messages.findIndex(message => message.time && message.time > eventTime)
  if (next >= 0) return Math.max(previous + 1, byTime >= 0 ? Math.min(next, byTime) : next)
  if (previous >= 0) return Math.max(previous + 1, byTime >= 0 ? byTime : 0)
  return byTime >= 0 ? byTime : final >= 0 ? final : messages.length
}

// Historical interrupt events are audit records, not permanently active forms.
// A newer Run on the same Thread supersedes the old checkpoint/question.
// In group chat, currentRunIds is authoritative: an optimistic local reply
// cannot consume an ask whose Resume failed before saving its input.
export function pendingQuestions(changes: OfficeHumanInputChange[], runs: OfficeRun[], currentRunIds?: string[]) {
  const pending = new Map<string, OfficeHumanInputChange>()
  const answered = new Set(changes.filter(change => change.type === 'responded').map(change => `${change.runId}:${change.requestId}`))
  for (const change of changes) {
    const key = `${change.runId}:${change.requestId}`
    const source = runs.find(run => run.runId === change.runId)
    if (change.type !== 'requested' || (currentRunIds === undefined && answered.has(key)) || source?.status !== 'interrupted') continue
    if (currentRunIds ? !currentRunIds.includes(change.runId)
      : runs.some(run => run.threadId === source.threadId && Date.parse(run.createdAt) > Date.parse(source.createdAt))) continue
    pending.set(key, change)
  }
  return [...pending.values()]
}

export function unobservedRunTargets<T extends { runId: string }>(targets: T[], runs: OfficeRun[], cursors: Map<string, number>, observing: T[]) {
  return targets.filter(target => {
    if (observing.some(item => item.runId === target.runId)) return false
    const run = runs.find(item => item.runId === target.runId)
    return !run || ['pending', 'running'].includes(run.status) || run.finalizing || run.eventsFinalized === false
      || !cursors.has(run.runId) || (run.lastEventSeq != null && (cursors.get(run.runId) || 0) < run.lastEventSeq)
  })
}

export function selectReplyQuestion(questions: OfficeHumanInputChange[], key: string, hasMentions: boolean) {
  if (hasMentions) return undefined
  return questions.find(change => `${change.runId}:${change.requestId}` === key)
    || (questions.length === 1 ? questions[0] : undefined)
}

// A later conversation is not evidence that this particular interrupt resumed.
export function hasResumedRun(run: OfficeRun | undefined, runs: OfficeRun[]) {
  return Boolean(run && runs.some(next => next.resumeSourceRunId === run.runId
    && next.employeeId === run.employeeId && next.threadId === run.threadId))
}

export function runStatusLabel(run: OfficeRun, resumed = false, waiting = false) {
  if (run.status === 'interrupted') {
    if (resumed) return t('ui.resumed')
    if (waiting) return t('ui.waiting_for_your_reply')
    return t('ui.interrupted')
  }
  switch (run.status) {
    case 'pending': return t('ui.queued')
    case 'running': return t('ui.working_2')
    case 'success': return t('ui.completed')
    case 'error': return t('ui.failed')
    case 'timeout': return t('ui.timeout')
  }
}

export function activityStatus(status?: string, runStatus?: string, waiting = false, answered = false) {
  if (status !== 'running' || !runStatus || ['pending', 'running'].includes(runStatus)) return status
  if (runStatus === 'interrupted' && answered) return 'answered'
  if (waiting) return 'waiting'
  if (runStatus === 'success') return 'done'
  return runStatus === 'interrupted' ? 'stopped' : 'error'
}


export function chatSender(employeeId: string | undefined, employees: OfficeEmployeeSummary[], snapshot?: OfficeGroupMessage) {
  const employee = employees.find(item => item.id === employeeId)
  const displayName = snapshot?.senderDisplayName || employee?.displayName || t('ui.employee')
  const role = employee?.role?.trim()
  return { name: role ? `${displayName}（${role}）` : displayName,
    avatar: snapshot ? snapshot.senderAvatar : employee?.avatar || '' }
}
