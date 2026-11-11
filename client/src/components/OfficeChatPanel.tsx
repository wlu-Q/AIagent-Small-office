import { t, useLocale, roleLabel } from '../i18n'
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'

import ChatSidebar from './office-chat/ChatSidebar'
import ChatWindow from './office-chat/ChatWindow'
import { WorkflowPanel, useWorkflow } from './office-chat/WorkflowPanel'
import HumanInputQuestion from './office-chat/HumanInputQuestion'
import ExecutionApprovalCard, { useExecutionApprovals } from './office-chat/ExecutionApprovalCard'
import { mergeConversationMessages, groupActivities, workProgressGroups, pendingQuestions, selectReplyQuestion, activityStatus, chatSender, unobservedRunTargets, hasResumedRun, runStatusLabel, isSavedRunMessage, runEventAnchor } from './office-chat/chatHistory'
import WorkProgress from './office-chat/WorkProgress'
import MessageContent, { FileReference } from './office-chat/MessageContent'
import { FilePreviewProvider } from './office-chat/FilePreview'
import TerminalOutlined from '@mui/icons-material/TerminalOutlined'
import CheckOutlined from '@mui/icons-material/CheckOutlined'
import StopRounded from '@mui/icons-material/StopRounded'
import ArrowUpwardRounded from '@mui/icons-material/ArrowUpwardRounded'
import { useGroupExecution } from './office-chat/useGroupExecution'
import {
  officeApi,
  type OfficeConversationMessage,
  type OfficeEmployeeSummary,
  type OfficeGroupMessage,
  type QueuedOfficeMessage,
  type OfficeRun,
} from '../services/office/officeApi'
import {
  isMissingRunError,
  projectOfficeHumanInputChanges,
  projectOfficeRunEvents,
  mergeVisibleRunEvents,
  readOfficeRunEvents,
  refreshOfficeEmployeeProjection,
  type OfficeHumanInputChange,
  type VisibleOfficeRunEvent,
} from '../services/office/officeEvents'
import type { Selection } from './office-chat/ChatSidebar'
import { additionalCharacterTextures } from '../characters/additionalCharacterTextures'
import { subscribeWorkspaceSummary, refreshWorkspaceSummary } from '../services/desktop/workspaceBridge'
import { RefreshTasks } from './office-chat/refreshTasks'
import { ConversationCache, conversationKey, type OutgoingMessage } from './office-chat/conversationCache'
import { OPEN_EMPLOYEE_CHAT_EVENT } from '../services/office/employeeChat'
import { hasDailyCharacterAssets } from '../characters/dailyCharacterAssets'

const avatarTextures = new Set<string>(['adam', 'ash', 'lucy', 'nancy', ...additionalCharacterTextures])
const HistoricalAvatar = styled.span<{ $texture: string }>`
  display: block;
  width: 27px;
  height: 27px;
  background-image: ${(props) => `url("assets/character/${hasDailyCharacterAssets(props.$texture) ? 'portraits/' : ''}${props.$texture}.png")`};
  background-position: ${(props) => hasDailyCharacterAssets(props.$texture) ? 'center' : '-3px -6px'};
  background-size: ${(props) => hasDailyCharacterAssets(props.$texture) ? '27px 27px' : 'auto'};
  image-rendering: pixelated;
`

const ChatMain = styled.main`
  min-width: 0;
  min-height: 0;
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  background: #ffffff;
`

const ConversationHeader = styled.header`
  flex: 0 0 auto;
  min-height: 58px;
  display: flex;
  align-items: center;
  padding: 0 22px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(248, 250, 252, 0.96);
  color: #0f172a;
  font-size: 15px;
  font-weight: 700;
  justify-content: space-between;
  gap: 16px;
  small { font-size: 11px; font-weight: 400; color: #7b8a9e; }
`

const HeaderActions = styled.div`
  /* Absorbs the free space so the title and status stay left-adjacent. */
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
`

const ResetButton = styled.button`
  border: 1px solid rgba(148, 163, 184, 0.5);
  background: #ffffff;
  color: #475569;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 10px;
  border-radius: 8px;
  cursor: pointer;

  &:hover:not(:disabled) { border-color: #94a3b8; color: #0f172a; }
  &:disabled { opacity: 0.6; cursor: default; }
`

const ResetConfirm = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 500;
  color: #b91c1c;

  button {
    border: 1px solid rgba(148, 163, 184, 0.5);
    background: #ffffff;
    color: #475569;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 9px;
    border-radius: 8px;
    cursor: pointer;
  }

  button[data-danger] { border-color: rgba(185, 28, 28, 0.45); color: #b91c1c; }
  button:disabled { opacity: 0.6; cursor: default; }
`

const MessageList = styled.div`
  min-height: 0;
  flex: 1 1 0;
  overflow-y: auto;
  padding: 24px clamp(20px, 4%, 52px);
  background: #ffffff;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
`

const MessageRow = styled.div<{ $role: OfficeConversationMessage['role'] }>`
  display: flex;
  flex-direction: column;
  align-items: ${(props) => (props.$role === 'user' ? 'flex-end' : 'flex-start')};
  justify-content: ${(props) => (props.$role === 'user' ? 'flex-end' : 'flex-start')};
  margin: 0 0 22px;
  min-width: 0;

  & + & {
    margin-top: 12px;
  }
`

const MessageIdentity = styled.div<{ $role: OfficeConversationMessage['role'] }>`
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 5px;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  flex-direction: ${(props) => (props.$role === 'user' ? 'row-reverse' : 'row')};
`

const MessageAvatar = styled.div<{ $role: OfficeConversationMessage['role'] }>`
  width: 27px;
  height: 27px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: ${(props) => (props.$role === 'user' ? '#2563eb' : '#64748b')};
  color: #ffffff;
  font-size: 12px;
  font-weight: 800;
`

function SenderAvatar({ role = 'assistant', name, avatar = '', employeeId }: {
  role?: OfficeConversationMessage['role']; name: string; avatar?: string; employeeId?: string
}) {
  useLocale()
  return <MessageAvatar data-sender-avatar={avatar} $role={role} aria-hidden="true">
    {employeeId === 'pm' && avatarTextures.has(avatar) ? <HistoricalAvatar $texture={avatar} /> : name.slice(0, 1)}
  </MessageAvatar>
}

const MessageBubble = styled.div<{ $role: OfficeConversationMessage['role'] }>`
  min-width: 0;
  max-width: ${(props) => (props.$role === 'user' ? 'min(82%, 720px)' : 'min(100%, 860px)')};
  padding: ${(props) => (props.$role === 'user' ? '12px 17px' : '4px 0 0 35px')};
  box-sizing: border-box;
  border-radius: 14px 4px 14px 14px;
  background: ${(props) => (props.$role === 'user' ? '#edf4fd' : 'transparent')};
  color: #1e293b;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  @media (max-width: 800px) { padding-left: 12px; }
`

const EmployeeProgress = styled.div`
  max-width: 860px;
  margin: 8px 0 20px 35px;
  min-width: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;

  [data-progress-identity] {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 4px;
    color: #64748b;
    font-size: 12px;
    font-weight: 600;
  }
  [data-progress-avatar] { width: 22px; height: 22px; }
  [data-progress-avatar] > div { transform: scale(.8); transform-origin: top left; }
  [data-progress-content] { padding-left: 29px; }
  [data-progress-truncated] { color: #64748b; font-size: 12px; }
  @media (max-width: 800px) { margin-left: 12px; }
`

const LoadingDots = styled.span`
  display: inline-flex;
  gap: 4px;
  align-items: center;

  &::before,
  &::after {
    content: '';
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #64748b;
    animation: office-chat-loading 1s ease-in-out infinite;
  }

  &::after {
    animation-delay: 0.18s;
  }

  @keyframes office-chat-loading {
    0%,
    100% {
      opacity: 0.35;
      transform: translateY(0);
    }

    50% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
`

const ConversationHint = styled.div`
  color: #94a3b8;
  font-size: 13px;
  text-align: center;
`

const RunEventList = styled.div`
  display: grid;
  gap: 7px;
  margin: 4px 0 18px 35px;
  max-width: calc(100% - 35px);
`

const RunEventRow = styled.div`
  padding: 8px 11px;
  border: 1px solid #dbe4ee;
  border-radius: 9px;
  background: rgba(248, 250, 252, 0.92);
  color: #64748b;
  font-size: 12px;
  line-height: 1.55;
`

const RunEventLabel = styled.strong`
  color: #334155;
  margin-right: 7px;
`

const ActivityRow = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin: 6px 0 10px 35px;
  color: #7a8797;
  font-size: 12px;
  line-height: 1.7;
  svg { font-size: 16px; flex-shrink: 0; }
  span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`

const ToolGroup = styled.details`
  margin: 6px 0 14px 35px; color: #7a8797; font-size: 12px;
  summary { cursor: pointer; padding: 7px 0; line-height: 1.6; }
  summary:focus-visible { outline: 2px solid #2563eb; outline-offset: 3px; }
  > div { border-left: 2px solid #e5eaf1; padding: 4px 12px; max-height: 380px; overflow: auto; }
  > div > div { margin-left: 0; }
  > div span { white-space: pre-wrap; overflow-wrap: anywhere; }
`

const RunStatusRow = styled.div`
  color: #7a8797;
  font-size: 12px;
  line-height: 1.7;
`

const QueuePanel = styled.section`
  flex: 0 0 auto;
  min-height: 0;
  padding: 10px clamp(20px, 4%, 52px);
  border-top: 1px solid #e2e8f0;
  background: #f8fafc;
`

const QueueHeading = styled.div`
  margin-bottom: 6px;
  color: #475569;
  font-size: 12px;
  font-weight: 600;
`

const QueueItems = styled.div`
  display: grid;
  gap: 8px;
  max-height: min(180px, 24vh);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  &:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
`

const QueueRow = styled(RunEventRow)`
  margin: 0;
  display: grid;
  gap: 8px;
`

const QueueMeta = styled.div`
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: #64748b;
  font-size: 12px;
`

const QueueText = styled.div`
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
`

const QueueInput = styled.input`
  width: 100%;
  box-sizing: border-box;
  padding: 7px 9px;
  border: 1px solid #cbd5e1;
  border-radius: 7px;
  color: #1e293b;
  font: inherit;
  font-size: 12px;
`

const QueueActions = styled.div`
  display: flex;
  gap: 7px;
  justify-content: flex-end;
`

const QueueButton = styled.button`
  padding: 5px 9px;
  border: 1px solid #cbd5e1;
  border-radius: 7px;
  background: #ffffff;
  color: #475569;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
`

const Composer = styled.div`
  position: relative;
  flex: 0 0 auto;
  padding: 14px 22px 16px;
  border-top: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(255, 255, 255, 0.96);
`

const MentionMenu = styled.div`
  position: absolute;
  left: 22px;
  bottom: 82px;
  z-index: 2;
  display: grid;
  gap: 3px;
  width: min(300px, calc(100% - 44px));
  max-height: 220px;
  overflow-y: auto;
  padding: 7px;
  border: 1px solid #dbe4ee;
  border-radius: 11px;
  background: #ffffff;
  box-shadow: 0 14px 30px rgba(15, 23, 42, 0.14);
`

const MentionItem = styled.button`
  padding: 8px 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  text-align: left;

  &:hover {
    background: #eff6ff;
  }
`

const ComposerEditor = styled.textarea`
  display: block;
  box-sizing: border-box;
  resize: none;
  font-family: inherit;
  width: 100%;
  min-height: 76px;
  max-height: 180px;
  overflow-y: auto;
  padding: 13px 15px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 14px;
  outline: none;
  background: #ffffff;
  color: #1e293b;
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  box-shadow: inset 0 1px 3px rgba(15, 23, 42, 0.04);

  &:focus {
    border-color: #5aa8e6;
    box-shadow: 0 0 0 3px rgba(90, 168, 230, 0.14);
  }

  &::placeholder {
    color: #94a3b8;
  }
`

const ComposerActions = styled.div`
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  small { margin-right: auto; color: #8592a3; font-size: 11px; }
`

const SendButton = styled.button`
  width: 36px;
  height: 36px;
  padding: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 50%;
  background: #2563eb;
  color: #ffffff;
  cursor: pointer;
  font: inherit;
  font-size: 12px;

  &:disabled {
    background: #b9c5d4;
    cursor: not-allowed;
  }
`

function createMessageId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `office-message-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function mentionQuery(text: string) {
  const match = text.match(/(?:^|\s)@([^\s@]*)$/)
  return match ? match[1] : null
}

type OfficeRunTarget = {
  employeeId: string
  runId: string
}

function isActiveRun(status: string) {
  return status === 'pending' || status === 'running'
}

function queueSourceLabel(sourceKind: string) {
  return sourceKind === 'pm_delegate' ? t('ui.assigned_by_pm') : t('ui.direct_task')
}

function queueMatchesSelection(item: QueuedOfficeMessage, selection: Selection) {
  return selection.kind === 'group'
    ? item.conversationKind === 'group'
    : item.conversationKind === 'private' && item.targetEmployeeId === selection.employeeId
}

function mergeGroupMessages(
  existing: OfficeGroupMessage[],
  incoming: OfficeGroupMessage[],
) {
  const byMessageId = new Map(existing.map((message) => [message.messageId, message]))
  incoming.forEach((message) => byMessageId.set(message.messageId, message))
  return [...byMessageId.values()].sort((left, right) => left.sequence - right.sequence)
}

export default function OfficeChatPanel() {
  useLocale()
  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState(0)
  const [projectId, setProjectId] = useState<string | null>()
  useEffect(() => {
    let project: string | undefined
    const unsubscribeSummary = subscribeWorkspaceSummary((summary) => {
      const next = summary?.projectId || summary?.path || ''
      setProjectId(summary?.projectId ?? null)
      if (project !== undefined && project !== next) setScope(value => value + 1)
      project = next
    })
    void refreshWorkspaceSummary().catch(() => undefined)
    // Drop all in-memory sessions immediately on rebinding. App/session unmount
    // also drops them; no data is persisted in a global cache or localStorage.
    const unsubscribeBridge = window.officeDesktopBridge?.onServiceEvent(event => {
      if (event.type === 'workspace.updated') { officeApi.invalidateEmployees(); project = undefined; setScope(value => value + 1) }
    })
    return () => { unsubscribeSummary(); unsubscribeBridge?.() }
  }, [])
  return <FilePreviewProvider key={scope}><OfficeChatSession open={open} setOpen={setOpen} projectId={projectId} onResetComplete={() => setScope(value => value + 1)} /></FilePreviewProvider>
}

function OfficeChatSession({ open, setOpen, projectId, onResetComplete }: { open: boolean; setOpen: React.Dispatch<React.SetStateAction<boolean>>; projectId: string | null | undefined; onResetComplete: () => void }) {
  useLocale()
  const mountedRef = useRef(true)
  useLayoutEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; observerRef.current?.tasks.clear(); replyGenerationRef.current += 1 }
  }, [])
  const [employees, setEmployees] = useState<OfficeEmployeeSummary[]>([])
  const [selection, setSelection] = useState<Selection>({ kind: 'group' })
  useEffect(() => {
    const openEmployee = (event: Event) => {
      const employeeId = (event as CustomEvent<unknown>).detail
      if (typeof employeeId !== 'string') return
      if (!employees.some(employee => employee.id === employeeId && employee.state === 'idle')) return
      setSelection({ kind: 'private', employeeId })
      setOpen(true)
    }
    window.addEventListener(OPEN_EMPLOYEE_CHAT_EVENT, openEmployee)
    return () => window.removeEventListener(OPEN_EMPLOYEE_CHAT_EVENT, openEmployee)
  }, [employees, setOpen])
  const approvals = useExecutionApprovals(projectId)
  const conversationApprovals = approvals.requests.filter(item => item.conversation_kind === selection.kind
    && (selection.kind === 'group' || item.conversation_employee_id === selection.employeeId))
  const approval = conversationApprovals[0]
  const cacheRef = useRef(new ConversationCache())
  const key = conversationKey(selection)
  const [viewKey, setViewKey] = useState(key)
  const [messagesReady, setMessagesReady] = useState(false)
  const workflowState = useWorkflow(open && selection.kind === 'group')
  const groupExecution = useGroupExecution(open && selection.kind === 'group')
  const groupExecutionRef = useRef(groupExecution.state)
  groupExecutionRef.current = groupExecution.state
  useEffect(() => {
    if (groupExecution.state?.paused && !groupExecution.state.stopping) {
      void refreshOfficeEmployeeProjection().catch(() => undefined)
    }
  }, [groupExecution.state?.paused, groupExecution.state?.stopping, groupExecution.state?.activeCount])
  const selectionRef = useRef(selection)
  selectionRef.current = selection
  const [messages, setMessages] = useState<OfficeConversationMessage[]>([])
  const [groupMessages, setGroupMessages] = useState<OfficeGroupMessage[]>([])
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [outgoing, setOutgoing] = useState<OutgoingMessage[]>([])
  const [showHistoryLoading, setShowHistoryLoading] = useState(false)
  const [runEvents, setRunEvents] = useState<VisibleOfficeRunEvent[]>([])
  const [trackedRuns, setTrackedRuns] = useState<OfficeRun[]>([])
  const waitingForReply = trackedRuns.some(run => isActiveRun(run.status))
  const [humanInputChanges, setHumanInputChanges] = useState<OfficeHumanInputChange[]>([])
  const [replyRequestKey, setReplyRequestKey] = useState('')
  const [submittingHumanInputId, setSubmittingHumanInputId] = useState<string | null>(null)
  const [queueItems, setQueueItems] = useState<QueuedOfficeMessage[]>([])
  const [queueEditId, setQueueEditId] = useState<string | null>(null)
  const [queueEditText, setQueueEditText] = useState('')
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [resetConfirming, setResetConfirming] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [error, setError] = useState('')
  const [refreshErrors, setRefreshErrors] = useState<Record<string, string>>({})
  const [draft, setDraft] = useState('')
  const [mentionEmployeeIds, setMentionEmployeeIds] = useState<string[]>([])
  const [mentionMenuOpen, setMentionMenuOpen] = useState(false)
  const editorRef = useRef<HTMLTextAreaElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const followLatestRef = useRef(true)
  const composingRef = useRef(false)
  const activeTargetsRef = useRef<OfficeRunTarget[]>([])
  const replyGenerationRef = useRef(0)
  const runReadRevisionRef = useRef(new Map<string, number>())
  const observerRef = useRef<{
    selection: Selection; generation: number; tasks: RefreshTasks;
    add: (targets: OfficeRunTarget[], employeeIds: string[]) => void
  } | null>(null)
  const groupMessagesRef = useRef<OfficeGroupMessage[]>([])
  const groupSequenceRef = useRef(0)
  const runEventCursorsRef = useRef(new Map<string, number>())
  const runEventHistoryRef = useRef(new Map<string, VisibleOfficeRunEvent[]>())

  const loadGroupMessages = async (startSequence: number) => {
    let afterSequence = startSequence
    const loadedMessages: OfficeGroupMessage[] = []
    while (true) {
      const response = await cacheRef.current.request(`group:${afterSequence}`, () => officeApi.listGroupMessages(afterSequence))
      loadedMessages.push(...response.messages)
      const nextAfterSequence = response.nextAfterSequence
      if (
        !mountedRef.current
        || response.messages.length === 0
        || nextAfterSequence <= afterSequence
        || response.messages.length < 100
      ) {
        const nextMessages = mergeGroupMessages([], loadedMessages)
        return { messages: nextMessages, nextAfterSequence }
      }
      afterSequence = nextAfterSequence
    }
  }


  useEffect(() => {
    let active = true
    const refreshEmployees = async () => {
      try {
        const nextEmployees = await cacheRef.current.request('employees', () => officeApi.listEmployees())
        if (active) setEmployees(nextEmployees)
      } catch (loadError) {
        if (active) setRefreshErrors(previous => ({ ...previous, employees: errorText(loadError) }))
      }
    }

    void refreshEmployees()
    const onEmployeesUpdated = (event: Event) => {
      const detail = (event as CustomEvent<OfficeEmployeeSummary[]>).detail
      if (Array.isArray(detail)) {
        setRefreshErrors(previous => { const next = { ...previous }; delete next.employees; return next })
        setEmployees(detail)
        if (
          selectionRef.current.kind === 'private'
          && !detail.some((employee) => selectionRef.current.kind === 'private' && employee.id === selectionRef.current.employeeId)
        ) {
          setSelection({ kind: 'group' })
        }
      } else { officeApi.invalidateEmployees(); void refreshEmployees() }
    }
    window.addEventListener('office-employees-updated', onEmployeesUpdated)
    return () => {
      active = false
      window.removeEventListener('office-employees-updated', onEmployeesUpdated)
    }
  }, [])

  useLayoutEffect(() => {
    let active = true
    observerRef.current?.tasks.clear()
    observerRef.current = null
    replyGenerationRef.current += 1
    setRefreshErrors({})
    const cached = cacheRef.current.get(selection)
    setViewKey(key)
    setMessagesReady(cached.ready)
    setMessages(cached.messages)
    setGroupMessages(cached.groupMessages)
    setOutgoing(cached.outgoing)
    setRunEvents(cached.events)
    setTrackedRuns(cached.runs)
    setHumanInputChanges(cached.humanInputs)
    setReplyRequestKey(cached.replyRequestKey)
    activeTargetsRef.current = []
    followLatestRef.current = cached.followLatest
    setDraft(cached.draft)
    setMentionEmployeeIds(cached.mentions)
    setMentionMenuOpen(false)
    setSubmittingHumanInputId(null)
    setQueueEditId(null)
    setQueueEditText('')
    runEventCursorsRef.current = cached.cursors
    runEventHistoryRef.current = cached.history
    groupMessagesRef.current = cached.groupMessages
    groupSequenceRef.current = cached.sequence
    setLoadingMessages(!cached.ready)
    setError('')
    const isCurrent = () => active && selection === selectionRef.current
    // Message visibility never waits for the queue or per-employee Run requests.
    void (async () => {
      try {
        if (selection.kind === 'group') {
          const loaded = await loadGroupMessages(cached.sequence)
          cached.groupMessages = mergeGroupMessages(cached.groupMessages, loaded.messages)
          cached.sequence = Math.max(cached.sequence, loaded.nextAfterSequence)
          if (isCurrent()) {
            groupMessagesRef.current = cached.groupMessages
            groupSequenceRef.current = cached.sequence
            setGroupMessages(cached.groupMessages)
          }
        } else {
          const result = await cacheRef.current.request(`private:${selection.employeeId}`, () => officeApi.listConversationMessages(selection.employeeId, 'private'))
          cached.messages = mergeConversationMessages(cached.messages, result)
          if (isCurrent()) setMessages(current => mergeConversationMessages(current, result))
        }
        cached.ready = true
        if (isCurrent()) setMessagesReady(true)
      } catch (loadError) {
        if (isCurrent()) setRefreshErrors(previous => ({ ...previous, messages: errorText(loadError) }))
      } finally {
        if (isCurrent()) setLoadingMessages(false)
      }
    })()
    // Discovery shares the observer's independent retry lanes, including when
    // the first employee-list/history request fails.
    observeReplies(selection, cached.runs.filter(run => isActiveRun(run.status)).map(run => ({ employeeId: run.employeeId, runId: run.runId })),
      selection.kind === 'private' ? [selection.employeeId] : employees.map(employee => employee.id))
    return () => { active = false; replyGenerationRef.current += 1; observerRef.current?.tasks.clear(); observerRef.current = null }

  }, [selection])

  useLayoutEffect(() => {
    if (viewKey !== key) return
    Object.assign(cacheRef.current.get(selection), {
      ready: messagesReady, messages, groupMessages, runs: trackedRuns, events: runEvents,
      humanInputs: humanInputChanges, replyRequestKey, draft, mentions: mentionEmployeeIds,
      sequence: groupSequenceRef.current, cursors: runEventCursorsRef.current, history: runEventHistoryRef.current,
      followLatest: followLatestRef.current,
    })
  })

  const selectedEmployee = selection.kind === 'private'
    ? employees.find((employee) => employee.id === selection.employeeId)
    : undefined
  const selectedEmployeeLabel = selectedEmployee
    ? selectedEmployee.role?.trim() ? `${selectedEmployee.displayName}（${roleLabel(selectedEmployee)}）` : selectedEmployee.displayName
    : undefined

  const refreshMessages = async (currentSelection: Selection, generation = replyGenerationRef.current) => {
    if (currentSelection.kind === 'group') {
      const afterSequence = groupSequenceRef.current
      const response = await cacheRef.current.request(`group:${afterSequence}`, () => officeApi.listGroupMessages(afterSequence))
      if (!mountedRef.current || generation !== replyGenerationRef.current || currentSelection !== selectionRef.current) return []
      groupSequenceRef.current = Math.max(groupSequenceRef.current, response.nextAfterSequence)
      const nextMessages = mergeGroupMessages(groupMessagesRef.current, response.messages)
      groupMessagesRef.current = nextMessages
      const cached = cacheRef.current.get(currentSelection)
      cached.groupMessages = nextMessages
      cached.sequence = Math.max(cached.sequence, groupSequenceRef.current)
      cached.ready = true
      setGroupMessages(nextMessages)
      setMessagesReady(true)
      return nextMessages
    }
    const nextMessages = await cacheRef.current.request(`private:${currentSelection.employeeId}`, () => officeApi.listConversationMessages(currentSelection.employeeId, 'private'))
    if (!mountedRef.current || generation !== replyGenerationRef.current || currentSelection !== selectionRef.current) return []
    const cached = cacheRef.current.get(currentSelection)
    const merged = mergeConversationMessages(cached.messages, nextMessages)
    cached.messages = merged
    setMessages(current => mergeConversationMessages(current, merged))
    setMessagesReady(true)
    return merged
  }

  const observeReplies = (
    currentSelection: Selection, targets: OfficeRunTarget[], employeeIds: string[] = [],
  ) => {
    if (!mountedRef.current || currentSelection !== selectionRef.current) return
    const generation = replyGenerationRef.current
    const existing = observerRef.current
    if (existing?.selection === currentSelection && existing.generation === generation) {
      existing.add(targets, employeeIds)
      return
    }
    existing?.tasks.clear()
    const tasks = new RefreshTasks()
    const observed = new Map<string, { target: OfficeRunTarget; run?: OfficeRun; terminalAt?: number; eventsStarted: number }>()
    const discovering = new Set<string>()
    const listedEmployees = new Set<string>()
    let rosterRead = currentSelection.kind === 'private'
    let queued = new Set<string>()
    let queueKnown = false
    let queueRevision = 0
    let messagesRead = false
    let observation: NonNullable<typeof observerRef.current>
    const current = () => mountedRef.current && currentSelection === selectionRef.current && generation === replyGenerationRef.current
    const issue = (key: string, value = '') => {
      if (!current()) return
      setRefreshErrors(previous => {
        if ((previous[key] || '') === value) return previous
        const next = { ...previous }
        if (value) next[key] = value
        else delete next[key]
        return next
      })
    }
    const syncTargets = () => { activeTargetsRef.current = [...observed.values()].map(item => item.target) }
    const finished = (item: typeof observed extends Map<string, infer T> ? T : never) => {
      const run = item.run
      return run && !isActiveRun(run.status) && !run.finalizing && run.eventsFinalized !== false
        && runEventCursorsRef.current.has(run.runId)
        && (run.lastEventSeq != null ? (runEventCursorsRef.current.get(run.runId) || 0) >= run.lastEventSeq
          : item.terminalAt !== undefined && item.eventsStarted >= item.terminalAt)
    }
    const stopIfIdle = () => {
      if (rosterRead && queueKnown && messagesRead && !observed.size && !discovering.size && !queued.size) {
        tasks.clear()
        if (observerRef.current === observation) observerRef.current = null
      }
    }
    const removeRun = (runId: string, missing = false) => {
      observed.delete(runId)
      tasks.remove(`status:${runId}`)
      tasks.remove(`events:${runId}`)
      issue(`status:${runId}`); issue(`events:${runId}`)
      if (missing) setTrackedRuns(runs => runs.filter(run => run.runId !== runId))
      syncTargets()
    }
    const addRun = (target: OfficeRunTarget) => {
      if (observed.has(target.runId)) return
      const item = { target, eventsStarted: -1 } as { target: OfficeRunTarget; run?: OfficeRun; terminalAt?: number; eventsStarted: number }
      observed.set(target.runId, item)
      syncTargets()
      const statusKey = `status:${target.runId}`
      const eventsKey = `events:${target.runId}`
      const failed = (key: string, error: unknown) => {
        if (!current()) return
        if (isMissingRunError(error)) { removeRun(target.runId, true); stopIfIdle() }
        else issue(key, errorText(error))
      }
      tasks.ensure(statusKey, async () => {
        const revision = runReadRevisionRef.current.get(target.runId) || 0
        const run = await cacheRef.current.request(`status:${target.runId}:${revision}`, () => officeApi.getRun(target.employeeId, target.runId))
        return { revision, run }
      }, ({ revision, run }) => {
        if (!current() || revision !== (runReadRevisionRef.current.get(target.runId) || 0)) return
        issue(statusKey)
        const terminal = !isActiveRun(run.status) && !run.finalizing && run.eventsFinalized !== false
        if (terminal && item.terminalAt === undefined) item.terminalAt = Date.now()
        if (!terminal) item.terminalAt = undefined
        item.run = run
        setTrackedRuns(runs => [...runs.filter(previous => previous.runId !== run.runId), run])
      }, error => failed(statusKey, error))
      tasks.ensure(eventsKey, async () => {
        const started = Date.now()
        const response = await cacheRef.current.request(`events:${target.runId}:${runEventCursorsRef.current.get(target.runId) || 0}`, () =>
          readOfficeRunEvents(target.employeeId, target.runId, runEventCursorsRef.current.get(target.runId)))
        return { started, response }
      }, ({ started, response }) => {
        if (!current()) return
        issue(eventsKey)
        item.eventsStarted = started
        runEventHistoryRef.current.set(target.runId, mergeVisibleRunEvents([
          ...(runEventHistoryRef.current.get(target.runId) || []), ...projectOfficeRunEvents(response.events),
        ]))
        const changes = projectOfficeHumanInputChanges(response.events)
        if (changes.length) setHumanInputChanges(previous => {
          const byEvent = new Map(previous.map(change => [`${change.runId}:${change.seq}`, change]))
          changes.forEach(change => byEvent.set(`${change.runId}:${change.seq}`, change))
          return [...byEvent.values()].sort((left, right) => left.seq - right.seq)
        })
        runEventCursorsRef.current.set(target.runId,
          Math.max(runEventCursorsRef.current.get(target.runId) || 0, response.nextAfterSeq || 0))
        setRunEvents([...runEventHistoryRef.current.values()].flat().sort((left, right) =>
          String(left.createdAt || '').localeCompare(String(right.createdAt || '')) || left.seq - right.seq))
      }, error => failed(eventsKey, error))
    }
    const discover = (employeeId: string) => {
      if (discovering.has(employeeId)) return
      discovering.add(employeeId)
      listedEmployees.add(employeeId)
      const key = `runs:${employeeId}`
      tasks.ensure(key, async () => {
        const revision = queueRevision
        const runs = await cacheRef.current.request(`runs:${conversationKey(currentSelection)}:${employeeId}`, () => officeApi.listRuns(employeeId, currentSelection.kind, 8))
        return { revision, runs }
      }, ({ revision, runs }) => {
        if (!current()) return
        issue(key)
        // Status reads own already-observed Runs; a delayed list cannot regress them.
        const fresh = runs.filter(run => !observed.has(run.runId))
        setTrackedRuns(previous => [...previous.filter(run => !fresh.some(next => next.runId === run.runId)), ...fresh])
        unobservedRunTargets(runs, runs, runEventCursorsRef.current, activeTargetsRef.current)
          .forEach(run => addRun({ employeeId: run.employeeId, runId: run.runId }))
        if (queueKnown && revision === queueRevision && !queued.has(employeeId)) {
          discovering.delete(employeeId)
          tasks.remove(key)
        }
        stopIfIdle()
      }, error => issue(key, errorText(error)))
    }
    const observeQueue = () => tasks.ensure('queue', () => officeApi.listQueue(), items => {
      if (!current()) return
      issue('queue')
      setQueueItems(items)
      const next = new Set(items.filter(item => queueMatchesSelection(item, currentSelection)
        && !(currentSelection.kind === 'group' && groupExecutionRef.current?.heldQueueIds.includes(item.queueId))).map(item => item.targetEmployeeId))
      if (!queueKnown || next.size !== queued.size || [...next].some(id => !queued.has(id))) queueRevision += 1
      queueKnown = true
      queued = next
      queued.forEach(discover)
      stopIfIdle()
    }, error => { queueKnown = false; issue('queue', errorText(error)) })
    observation = { selection: currentSelection, generation, tasks,
      add: (nextTargets, nextEmployees) => {
        if (nextEmployees.length) {
          queueKnown = false
          queueRevision += 1
          tasks.remove('queue')
        }
        nextTargets.forEach(addRun)
        nextEmployees.forEach(discover)
        observeQueue()
      } }
    observerRef.current = observation
    observation.add(targets, employeeIds)
    tasks.ensure('messages', async () => {
      // Capture the drained Runs BEFORE reading history. Their completion hooks
      // must have finished before this last authoritative conversation refresh.
      const drained = [...observed].filter(([, item]) => finished(item)).map(([id]) => id)
      await refreshMessages(currentSelection, generation)
      return drained
    }, drained => {
      if (!current()) return
      issue('messages'); messagesRead = true
      drained.forEach(id => { const item = observed.get(id); if (item && finished(item)) removeRun(id) })
      stopIfIdle()
    }, error => issue('messages', errorText(error)))
    tasks.ensure('employees', () => officeApi.listEmployees(), next => {
      if (!current()) return
      issue('employees'); rosterRead = true
      if (currentSelection.kind === 'group') next.forEach(employee => { if (!listedEmployees.has(employee.id)) discover(employee.id) })
      stopIfIdle()
    }, error => issue('employees', errorText(error)))
  }

  useEffect(() => {
    if (!open || selection.kind !== 'group' || !groupExecution.state) return
    const targets = unobservedRunTargets(groupExecution.state.runTargets, trackedRuns, runEventCursorsRef.current, activeTargetsRef.current)
    if (targets.length) void observeReplies(selection, targets)
  }, [open, selection, messagesReady, groupExecution.state])

  // Pending text belongs to its originating conversation, even if the user switches
  // while the IPC request is in flight. It is never inserted into server history.
  const updateOutgoing = (origin: Selection, update: (items: OutgoingMessage[]) => OutgoingMessage[]) => {
    const cached = cacheRef.current.get(origin)
    cached.outgoing = update(cached.outgoing)
    if (mountedRef.current && conversationKey(origin) === conversationKey(selectionRef.current)) setOutgoing(cached.outgoing)
  }

  const sendMessage = async () => {
    if (sending || submittingHumanInputId || stoppingConversation) return
    const text = editorRef.current?.value.trim() || ''
    if (!text) return
    followLatestRef.current = true
    if (replyInput) {
      if (await resumeHumanInput(replyInput, text)) setDraft('')
      return
    }

    const origin = selection
    const messageId = createMessageId()
    const mentions = [...mentionEmployeeIds]
    updateOutgoing(origin, items => [...items, { messageId, text, createdAt: Date.now(), status: 'sending' }])
    Object.assign(cacheRef.current.get(origin), { draft: '', mentions: [] })
    setDraft('')
    setMentionEmployeeIds([])
    setMentionMenuOpen(false)
    setSending(true)
    setError('')
    let accepted = false
    try {
      const result = await officeApi.sendMessage({
        messageId, conversationKind: origin.kind, text,
        targetEmployeeId: origin.kind === 'private' ? origin.employeeId : undefined,
        mentionEmployeeIds: origin.kind === 'group' ? mentions : [],
      })
      accepted = true
      updateOutgoing(origin, items => items.map(item => item.messageId === messageId ? { ...item, messageId: result.messageId, status: 'accepted' } : item))
      if (!mountedRef.current || conversationKey(origin) !== conversationKey(selectionRef.current)) return
      const currentSelection = selectionRef.current
      const targets = result.dispatches
        .filter(dispatch => dispatch.started && dispatch.runId)
        .map(dispatch => ({ employeeId: dispatch.employeeId, runId: dispatch.runId as string }))
      observeReplies(currentSelection, targets, result.dispatches.filter(dispatch => !dispatch.started).map(dispatch => dispatch.employeeId))

    } catch (sendError) {
      if (!accepted) updateOutgoing(origin, items => items.map(item => item.messageId === messageId ? { ...item, status: 'unconfirmed' } : item))
      if (mountedRef.current && conversationKey(origin) === conversationKey(selectionRef.current)) {
        setError(errorText(sendError))
        // A rejected response may arrive after persistence/dispatch. Reconcile,
        // but never resend automatically or overwrite a newly typed draft.
        try { await refreshMessages(selectionRef.current) } catch { /* Keep the visible text and original error. */ }
      }
    } finally {
      if (mountedRef.current) setSending(false)
    }
  }

  const resetGroupConversation = async () => {
    if (resetting) return
    setResetting(true)
    setError('')
    try {
      await officeApi.resetGroup()
    } catch (resetError) {
      if (mountedRef.current) {
        setError(errorText(resetError))
        setResetConfirming(false)
        setResetting(false)
      }
      return
    }
    // Bumping the session scope drops every in-memory trace of the old conversation —
    // messages, cursor, tracked runs, composer text — which is exactly what was just
    // deleted on the server. Only do it after the server confirms.
    setResetConfirming(false)
    setResetting(false)
    onResetComplete()
  }

  const handleEditorInput = (event: React.FormEvent<HTMLTextAreaElement>) => {
    const text = event.currentTarget.value
    setDraft(text)
    setMentionEmployeeIds(current => current.filter(id => {
      const employee = employees.find(item => item.id === id)
      return employee && text.includes(`@${employee.displayName}`)
    }))
    event.currentTarget.style.height = 'auto'
    event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 180)}px`
    setMentionMenuOpen(selection.kind === 'group' && mentionQuery(text) !== null)
  }

  const startQueueEdit = (item: QueuedOfficeMessage) => {
    setQueueEditId(item.queueId)
    setQueueEditText(item.text)
  }

  const saveQueueEdit = async (queueId: string) => {
    try {
      const edited = await officeApi.editQueue(queueId, queueEditText)
      setQueueItems((current) => current.map((item) => item.queueId === queueId ? edited : item))
      setQueueEditId(null)
      setQueueEditText('')
    } catch (queueError) {
      setError(errorText(queueError))
    }
  }

  const deleteQueueItem = async (queueId: string) => {
    try {
      await officeApi.deleteQueue(queueId)
      setQueueItems((current) => current.filter((item) => item.queueId !== queueId))
      if (queueEditId === queueId) {
        setQueueEditId(null)
        setQueueEditText('')
      }
    } catch (queueError) {
      setError(errorText(queueError))
    }
  }

  const cancelRun = async (run: OfficeRun) => {
    if (cancellingRunId || !isActiveRun(run.status)) return
    setCancellingRunId(run.runId)
    setError('')
    try {
      const cancelled = await officeApi.cancelRun(run.employeeId, run.runId)
      if (!mountedRef.current || selection !== selectionRef.current) return
      setTrackedRuns((current) => current.map((item) => item.runId === run.runId ? cancelled : item))
      observeReplies(selection, [{ employeeId: run.employeeId, runId: run.runId }])
    } catch (cancelError) {
      setError(errorText(cancelError))
    } finally {
      runReadRevisionRef.current.set(run.runId, (runReadRevisionRef.current.get(run.runId) || 0) + 1)
      setCancellingRunId(null)
    }
  }

  const pauseConversation = async () => {
    const origin = selectionRef.current
    try {
      if (origin.kind === 'group') {
        await groupExecution.pause()
        if (origin !== selectionRef.current) return
        trackedRuns.forEach(run => runReadRevisionRef.current.set(run.runId, (runReadRevisionRef.current.get(run.runId) || 0) + 1))
        observeReplies(origin, trackedRuns.map(run => ({ employeeId: run.employeeId, runId: run.runId })))
      } else {
        for (const run of trackedRuns.filter(run => isActiveRun(run.status))) await cancelRun(run)
      }
    } catch (e) { if (origin === selectionRef.current) setError(errorText(e)) }
  }
  const stoppingConversation = (selection.kind === 'group' && (groupExecution.busy || Boolean(groupExecution.state?.stopping))) || Boolean(cancellingRunId)
  const hasConversationWork = trackedRuns.some(run => isActiveRun(run.status))
    || (selection.kind === 'group' && ((groupExecution.state?.activeCount || 0) > 0
      || queueItems.some(item => item.conversationKind === 'group' && !groupExecution.state?.heldQueueIds.includes(item.queueId))))
  const showPause = !draft.trim() && (hasConversationWork || stoppingConversation)

  const resumeHumanInput = async (change: OfficeHumanInputChange, answer: string) => {
    const text = answer.trim()
    if (!text || submittingHumanInputId) return
    const sourceRun = trackedRuns.find((run) => run.runId === change.runId)
    if (!sourceRun) {
      setError(t('ui.no_run_awaiting_a_reply_was_found'))
      return
    }
    setSubmittingHumanInputId(change.requestId)
    setError('')
    try {
      const result = await officeApi.resumeRun(sourceRun.employeeId, sourceRun.runId, {
        source: change.source,
        requestId: change.requestId,
        ...(change.checkpointId ? { checkpointId: change.checkpointId } : {}),
        text,
      })
      if (!mountedRef.current || selection !== selectionRef.current) return
      setHumanInputChanges((current) => [
        ...current,
        { ...change, type: 'responded', seq: Number.MAX_SAFE_INTEGER },
      ])
      observeReplies(selection, result.started && result.runId ? [{ employeeId: sourceRun.employeeId, runId: result.runId }] : [],
        result.started ? [] : [sourceRun.employeeId])

      return true
    } catch (resumeError) {
      setError(errorText(resumeError))
    } finally {
      setSubmittingHumanInputId(null)
    }
  }

  const selectMention = (employee: OfficeEmployeeSummary) => {
    const editor = editorRef.current
    if (!editor) return
    const text = editor.value
    const query = mentionQuery(text)
    if (query === null) return
    const replacement = `@${employee.displayName} `
    const next = text.slice(0, text.length - query.length - 1) + replacement
    setDraft(next)
    setMentionEmployeeIds((current) => (
      current.includes(employee.id) ? current : [...current, employee.id]
    ))
    setMentionMenuOpen(false)
    editor.focus()
    requestAnimationFrame(() => editor.setSelectionRange(next.length, next.length))
  }

  const mentionQueryText = mentionQuery(draft)
  const mentionCandidates = mentionQueryText === null
    ? []
    : employees.filter((employee) => (
      employee.displayName.includes(mentionQueryText)
      || employee.id.toLowerCase().includes(mentionQueryText.toLowerCase())
    ))
  const visibleQueueItems = queueItems.filter((item) => queueMatchesSelection(item, selection))
  const pendingHumanInputs = pendingQuestions(humanInputChanges, trackedRuns,
    selection.kind === 'group' && groupExecution.state ? groupExecution.state.runTargets.map(target => target.runId) : undefined)
  const replyInput = selectReplyQuestion(pendingHumanInputs, replyRequestKey,
    selection.kind === 'group' && (mentionEmployeeIds.length > 0 || /(^|\s)@\S+/.test(draft)))

  const savedChatMessages = selection.kind === 'group'
    ? groupMessages.map((message) => ({
      id: message.messageId, role: message.senderKind === 'user' ? 'user' as const : 'assistant' as const,
      sourceMessageId: message.messageId,
      text: message.content, ...chatSender(message.senderId, employees, message),
      employeeId: message.senderKind === 'user' ? undefined : message.senderId,
      runId: message.sourceRunId, time: message.createdAt,
    }))
    : messages.map((message) => ({
      id: message.messageId, role: message.role, text: message.content,
      sourceMessageId: message.sourceMessageId,
      name: message.role === 'user' ? t('ui.you') : selectedEmployeeLabel || 'AI',
      avatar: message.role === 'user' ? '' : selectedEmployee?.avatar || '',
      employeeId: message.role === 'user' ? undefined : selectedEmployee?.id,
      runId: message.runId || runEvents.find((event) => event.kind === 'message' && event.messageId === message.messageId)?.runId || null,
      time: message.createdAt ? Date.parse(message.createdAt) : 0,
    }))
  const confirmedUserIds = new Set(savedChatMessages.filter(message => message.role === 'user')
    .flatMap(message => message.sourceMessageId ? [message.id, message.sourceMessageId] : [message.id]))
  const unsavedMessages = outgoing.filter(item => !confirmedUserIds.has(item.messageId))
  const chatMessages = [...savedChatMessages]
  for (const item of unsavedMessages) {
    // Keep authoritative history order; only position temporary messages.
    const nextIndex = chatMessages.findIndex(message => message.time > item.createdAt)
    chatMessages.splice(nextIndex < 0 ? chatMessages.length : nextIndex, 0, {
      id: item.messageId, role: 'user', text: item.text, name: t('ui.you'), avatar: '',
      sourceMessageId: item.messageId,
      employeeId: undefined, runId: null, time: item.createdAt,
    })
  }
  // Match the Office source ID as well as the native history ID. Never match
  // by text: two intentionally identical user messages are distinct messages.
  useEffect(() => {
    if (outgoing.some(item => confirmedUserIds.has(item.messageId))) updateOutgoing(selection, items => items.filter(item => !confirmedUserIds.has(item.messageId)))
  }, [messages, groupMessages, outgoing, selection])
  const timeline: Array<{ key: string; order: number; node: React.ReactNode; runId?: string | null; reply?: boolean; activity?: { runId: string; employeeName: string; status?: string } }> = chatMessages.map((message, index) => ({
    key: message.id, order: index * 1000,
    runId: message.role === 'assistant' ? message.runId : undefined,
    reply: message.role === 'assistant' && !runEvents.some(event => event.runId === message.runId && !event.taskId
      && event.kind === 'message' && event.messageId === message.id && event.hasToolCalls),
    node: <MessageRow data-group-message={selection.kind === 'group' ? message.id : undefined} data-message-role={message.role} $role={message.role}>
      <MessageIdentity $role={message.role}>
        <SenderAvatar role={message.role} name={message.name} avatar={message.avatar} employeeId={message.employeeId} />
        <span>{message.name}</span>
      </MessageIdentity>
      <MessageBubble $role={message.role}>
        <MessageContent text={message.text} employeeId={message.employeeId} runId={message.runId} />
      </MessageBubble>
      {unsavedMessages.find(item => item.messageId === message.id)?.status === 'sending' && <RunStatusRow role="status" data-office-send-status="sending">{t('ui.sending')}</RunStatusRow>}
      {unsavedMessages.find(item => item.messageId === message.id)?.status === 'unconfirmed' && <RunStatusRow role="status" data-office-send-status="unconfirmed">
        {t('ui.delivery_unconfirmed_your_original_message_is_preserved')}<button type="button" onClick={() => { void refreshMessages(selection).catch(loadError => setError(errorText(loadError))) }}>{t('ui.check_again')}</button>
      </RunStatusRow>}
    </MessageRow>,
  }))
  runEvents.forEach((event, index) => {
    event = { ...event, label: event.labelKey ? t(event.labelKey) : event.label, detail: event.detailKey ? t(event.detailKey) : event.detail }
    if (isSavedRunMessage(event, chatMessages, runEvents)) return
    const owner = trackedRuns.find((run) => run.runId === event.runId)?.employeeId
      || activeTargetsRef.current.find(target => target.runId === event.runId)?.employeeId
    // Assignment events describe the owner's action, not the assignee speaking.
    const speakerId = (event.kind === 'assignment' ? owner : event.employeeId || owner) || selectedEmployee?.id
    const historicalSender = groupMessages.find(message => message.sourceRunId === event.runId && message.senderId === speakerId)
    const { name: employeeName, avatar: employeeAvatar } = chatSender(speakerId, employees, historicalSender)
    const activityDetail = event.kind === 'assignment'
      ? `${chatSender(event.employeeId, employees).name}：${event.detail}` : event.detail
    const activityLabel = event.kind === 'tool' ? t('ui.calling_tool_value', { value1: (event.label) }) : event.label
    const anchor = runEventAnchor(event, chatMessages, runEvents)
    timeline.push({
      key: `event:${event.runId}:${event.taskId || ''}:${event.kind}:${event.messageId || event.callId || event.seq}`,
      runId: event.runId,
      reply: event.kind === 'message' && !event.taskId && !event.hasToolCalls
        && !runEvents.some(next => next.runId === event.runId && !next.taskId && next.seq > event.seq),
      order: anchor * 1000 - 500 + index / (runEvents.length + 1) * 400,
      activity: !['message', 'artifact'].includes(event.kind) ? { runId: event.runId, employeeName, status: activityStatus(event.status, trackedRuns.find(run => run.runId === event.runId)?.status, pendingHumanInputs.some(change => change.runId === event.runId), event.label === 'ask_clarification' && hasResumedRun(trackedRuns.find(run => run.runId === event.runId), trackedRuns)) } : undefined,
      node: event.kind === 'message' && event.taskId ? <EmployeeProgress data-office-employee-progress={speakerId} data-progress-message={event.messageId} data-progress-task={event.taskId}>
        <div data-progress-identity><span data-progress-avatar><SenderAvatar name={employeeName} avatar={employeeAvatar} employeeId={speakerId} /></span><span>{employeeName}</span></div>
        <div data-progress-content><MessageContent text={event.detail} employeeId={owner} runId={event.runId} workspaceId={event.workspaceId} /></div>
        {event.truncated && <div data-progress-truncated>{t('ui.execution_text_is_too_long_an_excerpt_is_shown')}</div>}
      </EmployeeProgress> : event.kind === 'message' ? <MessageRow $role="assistant" data-office-progressive-reply data-progress-message={event.messageId}>
        <MessageIdentity $role="assistant"><SenderAvatar name={employeeName} avatar={employeeAvatar} employeeId={speakerId} /><span>{employeeName}</span></MessageIdentity>
        <MessageBubble $role="assistant"><MessageContent text={event.detail} employeeId={owner} runId={event.runId} /></MessageBubble>
      </MessageRow> : event.kind === 'artifact' && event.filePath ? <ActivityRow>
        <FileReference path={event.filePath} employeeId={owner} runId={event.runId} />
      </ActivityRow> : <ActivityRow data-office-run-events data-office-activity={event.kind} title={`${employeeName} · ${activityLabel}${activityDetail ? `：${activityDetail}` : ''}`}>
        {event.status === 'done' ? <CheckOutlined aria-hidden="true" /> : <TerminalOutlined aria-hidden="true" />}
        <span>{employeeName} · {activityLabel}{event.status === 'error' ? t('ui.failed_2') : ''}{activityDetail ? `${event.kind === 'tool' ? '：' : '  '}${activityDetail}` : ''}</span>
      </ActivityRow>,
    })
  })
  trackedRuns.forEach((run) => {
    const messageOrders = chatMessages.flatMap((message, index) => message.runId === run.runId ? [index * 1000] : [])
    const eventOrders = timeline.filter((item) => item.key.startsWith(`event:${run.runId}:`)).map((item) => item.order)
    const anchors = [...messageOrders, ...eventOrders]
    // A restored success without visible messages/events is not another new chat message.
    if (!anchors.length && run.status === 'success') return
    const terminalTime = Date.parse(run.updatedAt || run.createdAt || '')
    const nextMessage = !isActiveRun(run.status) && Number.isFinite(terminalTime)
      ? chatMessages.findIndex(message => message.time > terminalTime) : -1
    // Runs that fail before producing a reply still have a historical time.
    // Anchor them there so later messages do not carry old errors at the bottom.
    const fallbackOrder = nextMessage >= 0 ? nextMessage * 1000 - 100 : chatMessages.length * 1000 + 50
    const { name: employeeName } = chatSender(run.employeeId, employees, groupMessages.find(message => message.sourceRunId === run.runId && message.senderId === run.employeeId))
    timeline.push({
      key: `status:${run.runId}`,
      runId: run.runId,
      order: anchors.length ? Math.max(...anchors) + 50 : fallbackOrder,
      node: <RunStatusRow data-office-run-status={run.runId} style={{ margin: '0 0 18px 35px' }}>
        {employeeName} · {runStatusLabel(run, hasResumedRun(run, trackedRuns), pendingHumanInputs.some(change => change.runId === run.runId))}
        {run.error && <span role="alert"> · {run.error}</span>}
      </RunStatusRow>,
    })
  })
  timeline.sort((left, right) => left.order - right.order)

  const renderActivities = (items: typeof timeline) => groupActivities(items).map(group => group[0].activity ? <ToolGroup key={group[0].key} data-office-tool-group>
    <summary>{group[0].activity.employeeName} · {group.length} {t('ui.tool_operations')}{group.some(item => item.activity?.status === 'error') ? t('ui.includes_failures') : group.some(item => item.activity?.status === 'running') ? t('ui.running_2') : group.some(item => item.activity?.status === 'waiting') ? t('ui.awaiting_reply_2') : group.some(item => item.activity?.status === 'answered') ? t('ui.replied') : group.some(item => item.activity?.status === 'stopped') ? t('ui.stopped_2') : t('ui.execution_record')}
    </summary>
    <div>{group.map(item => <React.Fragment key={item.key}>{item.node}</React.Fragment>)}</div>
  </ToolGroup> : <React.Fragment key={group[0].key}>{group[0].node}</React.Fragment>)
  const foldedKeys = new Set<string>()
  const workRows: typeof timeline = []
  for (const group of workProgressGroups(trackedRuns, new Set(pendingHumanInputs.map(change => change.runId)))) {
    const runIds = new Set(group.runs.map(run => run.runId))
    const rows = timeline.filter(item => item.runId && runIds.has(item.runId))
    if (!rows.length) continue
    const finalReply = [...rows].reverse().find(item => item.reply && item.runId === group.latest.runId)
    const steps = rows.filter(item => item !== finalReply)
    const hasSteps = steps.some(item => !item.key.startsWith('status:'))
    steps.forEach(item => foldedKeys.add(item.key))
    workRows.push({ key: `work:${group.key}`, order: Math.min(...rows.map(item => item.order)) - 1,
      node: <WorkProgress workKey={group.key} startedAt={group.startedAt} finishedAt={group.finishedAt}
        active={group.active} waiting={group.waiting} status={group.latest.status} hasSteps={hasSteps}>
        {renderActivities(steps)}
      </WorkProgress> })
    if (!hasSteps && group.latest.error) workRows.push({ key: `error:${group.key}`, order: rows[0].order,
      node: <RunStatusRow role="alert">{group.latest.error}</RunStatusRow> })
  }
  const displayTimeline = [...timeline.filter(item => !foldedKeys.has(item.key)), ...workRows]
    .sort((left, right) => left.order - right.order)

  useLayoutEffect(() => {
    if (viewKey !== key || !listRef.current) return
    const cached = cacheRef.current.get(selection)
    listRef.current.scrollTop = followLatestRef.current ? listRef.current.scrollHeight : cached.scrollTop
  }, [viewKey, messages, groupMessages, runEvents, trackedRuns, humanInputChanges, waitingForReply, loadingMessages, outgoing, open])

  useLayoutEffect(() => {
    if (editorRef.current) {
      editorRef.current.style.height = 'auto'
      editorRef.current.style.height = `${Math.min(editorRef.current.scrollHeight, 180)}px`
    }
  }, [draft, open])

  const pendingRunTargets = trackedRuns.filter(run => isActiveRun(run.status))

  useEffect(() => {
    setShowHistoryLoading(false)
    if (!loadingMessages) return
    const timer = window.setTimeout(() => setShowHistoryLoading(true), 250)
    return () => window.clearTimeout(timer)
  }, [loadingMessages, key])


  return (
    <ChatWindow
      open={open}
      approvalCount={approvals.requests.length}
      onToggle={() => setOpen((current) => !current)}
      onClose={() => setOpen(false)}
      sidebar={(
        <ChatSidebar
          employees={employees}
          selection={selection}
          onSelect={next => { if (conversationKey(next) !== key) setSelection(next) }}
        />
      )}
    >
      <ChatMain aria-label={t('ui.current_chat_window')} data-office-chat-main>
        <ConversationHeader>
          <span>{selectedEmployee ? `${selectedEmployee.displayName} ${selectedEmployee.role?.trim() ? t('chat.employee_direct', { role: roleLabel(selectedEmployee) }) : t('chat.direct')}` : t('ui.office_group_chat')}</span>
          <small>{waitingForReply ? t('ui.collaborating_replies_update_live') : t('ui.conversation_and_work_history')}</small>
          {selection.kind === 'group' && (
            <HeaderActions>
              {resetConfirming ? (
                <ResetConfirm role="alert">
                  <span>{t('ui.clear_group_chat_history_context_and_the_current_workflow_employee_direct_messages')}</span>
                  <button data-danger type="button" disabled={resetting} onClick={() => { void resetGroupConversation() }}>
                    {resetting ? t('ui.resetting') : t('ui.confirm_reset')}
                  </button>
                  <button type="button" disabled={resetting} onClick={() => setResetConfirming(false)}>{t('ui.cancel')}</button>
                </ResetConfirm>
              ) : (
                <ResetButton type="button" onClick={() => setResetConfirming(true)}>{t('ui.reset_chat_history')}</ResetButton>
              )}
            </HeaderActions>
          )}
        </ConversationHeader>
        <WorkflowPanel workflow={workflowState.workflow} error={workflowState.error} />
        <MessageList ref={listRef} data-office-chat-messages onScroll={(event) => {
          const list = event.currentTarget
          followLatestRef.current = list.scrollHeight - list.scrollTop - list.clientHeight < 80
          if (viewKey === key) Object.assign(cacheRef.current.get(selection), { scrollTop: list.scrollTop, followLatest: followLatestRef.current })
        }}>
          {loadingMessages && showHistoryLoading && chatMessages.length === 0 && <ConversationHint data-office-message-loading>{t('ui.loading_messages')}</ConversationHint>}
          {!loadingMessages && selection.kind === 'group' && chatMessages.length === 0 && (
            <ConversationHint>{t('ui.no_group_messages_yet')}</ConversationHint>
          )}
          {!loadingMessages && selection.kind === 'private' && chatMessages.length === 0 && (
            <ConversationHint>{t('ui.no_direct_messages_yet')}</ConversationHint>
          )}
          {renderActivities(displayTimeline)}
          {pendingHumanInputs.length > 0 && (
            <RunEventList data-office-human-inputs>
              {pendingHumanInputs.map((change) => (
                <HumanInputQuestion key={`${change.runId}:${change.requestId}`} change={change}
                  senderName={chatSender(trackedRuns.find(run => run.runId === change.runId)?.employeeId, employees).name}
                  answer={replyInput === change ? draft : ''} disabled={submittingHumanInputId === change.requestId || stoppingConversation}
                  onAnswer={text => { setMentionEmployeeIds([]); setReplyRequestKey(`${change.runId}:${change.requestId}`); setDraft(text) }}
                  onSubmit={text => { void resumeHumanInput(change, text).then(sent => { if (sent) setDraft('') }) }} />
              ))}
            </RunEventList>
          )}
          {waitingForReply && pendingRunTargets.map(target => {
            const employee = employees.find(item => item.id === target.employeeId)
            return <MessageRow key={target.runId} $role="assistant" data-office-ai-loading data-office-loading-run-id={target.runId}>
              <MessageIdentity $role="assistant">
                <SenderAvatar name={employee?.displayName || target.employeeId} avatar={employee?.avatar} employeeId={target.employeeId} />
                <span>{employee?.displayName || target.employeeId}</span>
              </MessageIdentity>
              <MessageBubble $role="assistant"><LoadingDots aria-label={t('ui.value_is_replying', { value1: (employee?.displayName || target.employeeId) })} /></MessageBubble>
            </MessageRow>
          })}

        </MessageList>
          {visibleQueueItems.length > 0 && (
            <QueuePanel data-office-queue aria-label={t('ui.pending_messages')}>
              <QueueHeading>{t('ui.pending_messages_2')}{visibleQueueItems.length}</QueueHeading>
              <QueueItems data-office-queue-items tabIndex={0} aria-label={t('ui.queued_messages')}>
              {visibleQueueItems.map((item) => {
                const queuedEmployee = employees.find((employee) => employee.id === item.targetEmployeeId)
                const employeeName = queuedEmployee ? `${queuedEmployee.displayName}${queuedEmployee.role?.trim() ? `（${roleLabel(queuedEmployee)}）` : ''}` : item.targetEmployeeId
                return (
                  <QueueRow data-office-queue-item={item.queueId} key={item.queueId}>
                    <QueueMeta>
                      <span>{employeeName} · {queueSourceLabel(item.sourceKind)}</span>
                      <span>{groupExecution.state?.heldQueueIds.includes(item.queueId) ? t('ui.paused') : t('ui.queued')}</span>
                    </QueueMeta>
                    {queueEditId === item.queueId ? (
                      <QueueInput
                        data-office-queue-input={item.queueId}
                        value={queueEditText}
                        onChange={(event) => setQueueEditText(event.target.value)}
                      />
                    ) : (
                      <QueueText>{item.text}</QueueText>
                    )}
                    <QueueActions>
                      {queueEditId === item.queueId ? (
                        <QueueButton type="button" data-office-queue-save={item.queueId} onClick={() => void saveQueueEdit(item.queueId)}>{t('ui.confirm')}</QueueButton>
                      ) : (
                        <QueueButton type="button" data-office-queue-edit={item.queueId} onClick={() => startQueueEdit(item)}>{t('ui.edit')}</QueueButton>
                      )}
                      <QueueButton type="button" data-office-queue-delete={item.queueId} onClick={() => void deleteQueueItem(item.queueId)}>{t('ui.delete')}</QueueButton>
                    </QueueActions>
                  </QueueRow>
                )
              })}
            </QueueItems>
            </QueuePanel>
          )}
        <Composer>
          {approval && <ExecutionApprovalCard request={approval} count={conversationApprovals.length}
            saving={approvals.saving} error={approvals.error?.id === approval.approval_id ? approvals.error.message : undefined}
            onDecide={decision => void approvals.decide(approval, decision)} />}
          <div hidden={!!approval}>
          <ComposerEditor
            ref={editorRef}
            value={draft}
            role="textbox"
            aria-label={selection.kind === 'private' ? t('ui.employee_direct_message_input') : t('ui.team_chat_input')}
            aria-multiline="true"
            data-office-message-input
            placeholder={replyInput ? t('ui.type_your_answer_then_press_enter') : selection.kind === 'group' ? t('ui.send_a_message_or_an_employee_to_collaborate') : t('ui.type_a_message')}
            spellCheck={false}
            onChange={handleEditorInput}
            onCompositionStart={() => { composingRef.current = true }}
            onCompositionEnd={() => { composingRef.current = false }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') setMentionMenuOpen(false)
              if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || composingRef.current || event.keyCode === 229) return
              event.preventDefault()
              if (mentionMenuOpen && mentionCandidates.length > 0) selectMention(mentionCandidates[0])
              else void sendMessage()
            }}
          />
          {mentionMenuOpen && mentionCandidates.length > 0 && (
            <MentionMenu role="listbox" data-office-mention-menu>
              {mentionCandidates.map((employee) => (
                <MentionItem
                  key={employee.id}
                  type="button"
                  role="option"
                  data-office-mention={employee.id}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectMention(employee)}
                >
                  @{employee.displayName}
                </MentionItem>
              ))}
            </MentionMenu>
          )}
          <ComposerActions>
            <small>{t('ui.enter_to_send_shift_enter_for_a_new_line')}</small>
            <SendButton
              type="button"
              data-office-send={!showPause ? true : undefined}
              data-office-pause={showPause ? true : undefined}
              aria-label={showPause ? (selection.kind === 'group' ? t('ui.pause_all_group_tasks') : t('ui.pause_current_employee')) : t('ui.send_message')}
              title={showPause ? t('ui.stop_this_conversation_s_execution_and_keep_existing_files_other_conversations_continue') : t('ui.send_message_enter')}
              disabled={stoppingConversation || sending || Boolean(submittingHumanInputId) || (!showPause && !draft.trim())}
              onClick={() => { if (showPause) void pauseConversation(); else void sendMessage() }}
            >
              {showPause ? <StopRounded fontSize="small" /> : <ArrowUpwardRounded fontSize="small" />}
            </SendButton>
          </ComposerActions>
          {stoppingConversation && <ConversationHint role="status">{t('ui.stopping_tasks_please_wait')}</ConversationHint>}
          {groupExecution.state?.paused && !groupExecution.state.error && !stoppingConversation && <ConversationHint role="status">{t('ui.group_tasks_are_paused_and_existing_files_are_kept_continue_chatting_or')}</ConversationHint>}
          {groupExecution.state?.error && <ConversationHint role="alert">{groupExecution.state.error}</ConversationHint>}
          {groupExecution.error && <ConversationHint role="alert">{t('ui.group_execution_status')}{groupExecution.error}</ConversationHint>}
          {Object.keys(refreshErrors).length > 0 && <ConversationHint role="status" data-office-refresh-error title={Object.values(refreshErrors).join('；')}>{t('ui.some_information_could_not_refresh_retrying_while_keeping_displayed_content')}</ConversationHint>}
          {error && <ConversationHint role="alert">{error}</ConversationHint>}
          </div>
        </Composer>
      </ChatMain>
    </ChatWindow>
  )
}
