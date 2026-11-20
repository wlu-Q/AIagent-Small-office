import { EmployeeProjection } from './employeeProjection'

export type OfficeFileReference = { path: string; employeeId?: string; runId?: string; projectId?: string }
export type OfficeFilePreview = {
  path: string; name: string; size: number; kind: 'text' | 'image' | 'unsupported';
  language: string; content?: string; dataUrl?: string; reason?: string
}

export type OfficeConversationKind = 'private' | 'group'
export type OfficeThinkingLevel = 'off' | 'low' | 'medium' | 'high'
export type OfficeEmployeeState = 'idle' | 'working' | 'disabled'
export type OfficeRunStatus =
  | 'pending'
  | 'running'
  | 'success'
  | 'error'
  | 'timeout'
  | 'interrupted'

export type OfficeEmployeeSummary = {
  id: string
  templateId: string
  role: string
  displayName: string
  avatar: string
  modelProfileId: string | null
  workstationId: string
  isPm: boolean
  state: OfficeEmployeeState
  statusDetail: string
  lastReport: string
  currentTaskId: string | null
  enabled: boolean
  disabledReason: string
  createdAt: number
  updatedAt: number
  canDelegate: boolean
}

export type OfficeEmployeeTemplateSummary = {
  templateId: string
  role: string
  category: string
  tags: string[]
  specialties: string[]
  prompt?: string
  skills?: string[]
  toolGroups?: string[]
}

export type OfficeSkillSummary = {
  name: string
  description: string
  category: string
  enabled: boolean
}

export type OfficeModelSummary = {
  name: string
  model: string
  display_name: string | null
  description: string | null
  supports_thinking: boolean
  supports_reasoning_effort: boolean
}

export type OfficeBootstrapInput = {
  companyName: string
  modelName: string
  baseUrl: string
  apiKey: string
}

export type OfficeBootstrapModel = {
  name: string
  model: string
  displayName: string
}

export type OfficeBootstrapStatus = {
  terminalAccessMode?: 'approval' | 'full_access'
  initialized: boolean
  companyName: string | null
  defaultModel: OfficeBootstrapModel | null
  thinkingLevel: OfficeThinkingLevel
  baseUrl: string | null
  apiKeyConfigured: boolean
}

export type OfficeSettingsInput = {
  companyName: string
  terminalAccessMode?: 'approval' | 'full_access'
  confirmFullAccess?: boolean
}

export type OfficeExecutionApproval = {
  approval_id: string; request_hash: string; employee_id: string; employee_name?: string
  command: string; resolved_cwd: string; expires_at: number
  conversation_kind: 'group' | 'private' | null; conversation_employee_id: string | null
  project_id: string | null
}
export type OfficeEmployeeTools = {
  mcpServices: { id: string; name: string }[]
  tools: string[]; revision: string; requiredTools: string[]
  availableTools: string[]; unavailableTools: Record<string, string>
  choices: { id: string; label: string; description: string; members: string[]; required: boolean; unavailableReason: string }[]
}

export type OfficeThinkingOption = { value: string; label: string }
export type OfficeModelProvider = {
  id: string; name: string; baseUrl: string
  thinkingOptions: OfficeThinkingOption[]; defaultThinkingLevel: string
}
export type OfficeManagedModel = {
  id: string; provider: string | null; providerName: string; modelName: string
  createdAt: string | null; supportsVision: boolean; isDefault: boolean
  thinkingLevel: string | null; thinkingLabel: string
}
export type OfficeModelCatalogue = { models: OfficeManagedModel[]; providers: OfficeModelProvider[] }
export type OfficeAddModelInput = {
  requestId: string; provider: string; modelName: string; baseUrl: string; apiKey: string; supportsVision: boolean
  thinkingLevel?: string
}
export type OfficeEditModelInput = Omit<OfficeAddModelInput, 'requestId' | 'provider'>
export type OfficeModelDetail = OfficeManagedModel & {
  baseUrl: string; apiKeyConfigured: boolean; editable: boolean; editReasonCode: string
}

export type OfficeMemoryFact = {
  id: string
  content: string
  category: string
}

export type OfficeMemoryResponse = {
  facts: OfficeMemoryFact[]
}

export type EmployeeMemoryFact = OfficeMemoryFact & {
  confidence: number
  createdAt: string
  source: string
  sourceError?: string | null
}

export type WorkLogDays = { days: { date: string; count: number }[]; nextBefore: string | null }
export type WorkLogDay = { date: string; tasks: {
  id: string; source: 'private' | 'group' | 'delegation'; task: string | null
  startedAt: string | null; endedAt: string | null; recordedAt: string
  steps: { text: string; error: boolean }[]; result: string | null; issue: string | null; outcome: string
}[] }

export type EmployeeMemoryResponse = {
  lastUpdated: string
  facts: EmployeeMemoryFact[]
  user: Record<string, { summary: string; updatedAt: string }>
  history: Record<string, { summary: string; updatedAt: string }>
}

export type OfficeMemoryFactInput = {
  content: string
  category: string
  confidence: number
}

export type OfficeCreateEmployeeInput = {
  id: string
  templateId: string
  displayName: string
  avatar: string
  prompt: string
  skills: string[]
}

export type OfficeEmployeePersonalInfoInput = {
  displayName?: string
  prompt?: string
  skills?: string[]
  modelProfileId?: string
}

export type OfficeEmployeeDetail = {
  employee: OfficeEmployeeSummary
  personalInfo: Required<OfficeEmployeePersonalInfoInput>
  effectivePackage: {
    agentId: string
    displayName: string
    agentKind: string
    canDelegate: boolean
    capabilities: string[]
    tools: string[]
    toolGroupMembers: Record<string, string[]>
    permissions: string[]
    ownedDeliverables: string[]
  }
  installedSkills: Array<{ skillId: string; enabled: boolean }>
  authorizedTools: Array<{ toolId: string; enabled: boolean }>
  capabilityIndex: {
    effectiveCapabilities: string[]
    effectiveTools: string[]
    effectivePermissions: string[]
    [key: string]: unknown
  }
}

export type OfficeMessageCommand = {
  messageId: string
  conversationKind: OfficeConversationKind
  text: string
  targetEmployeeId?: string
  mentionEmployeeIds: string[]
}

export type OfficeMessageDispatch = {
  employeeId: string
  queueId: string | null
  started: boolean
  runId: string | null
}

export type OfficeMessageResult = {
  messageId: string
  conversationKind: OfficeConversationKind
  triggerEmployeeIds: string[]
  preselectedEmployeeIds: string[]
  dispatches: OfficeMessageDispatch[]
}

export type OfficeGroupMessage = {
  messageId: string
  sequence: number
  senderKind: string
  senderId: string
  senderDisplayName: string
  senderAvatar: string
  content: string
  mentionEmployeeIds: string[]
  sourceRunId: string | null
  createdAt: number
}

export type OfficeConversationMessage = {
  messageId: string
  sourceMessageId?: string
  runId: string | null
  role: 'user' | 'assistant'
  content: string
  createdAt: string | null
}

export type QueuedOfficeMessage = {
  queueId: string
  targetEmployeeId: string
  sourceKind: string
  conversationKind: OfficeConversationKind
  text: string
  enqueuedAt: number
}

export type OfficeRunResumeInput = {
  source: string
  requestId: string
  checkpointId?: string
  text: string
}

export type OfficeRunResumeResult = {
  employeeId: string
  sourceRunId: string
  queueId: string | null
  started: boolean
  runId: string | null
}

export type OfficeRun = {
  runId: string
  projectId: string | null
  workId: string | null
  resumeSourceRunId: string | null
  workStartedAt?: string | null
  runtimeFinishedAt?: string | null
  employeeId: string
  taskId: string
  taskSummary: string
  threadId: string
  assistantId: string
  status: OfficeRunStatus
  createdAt: string
  updatedAt: string
  error: string | null
  modelName: string | null
  modelProfileId: string | null
  runtimeModelId: string | null
  llmCallCount: number
  messageCount: number
  lastAiMessage: string | null
  eventsFinalized: boolean
  finalizing: boolean
  lastEventSeq: number
  tokenUsage: { input: number; output: number; total: number }
}

export type OfficeRunEvent = {
  thread_id: string
  run_id: string
  user_id: string
  event_type: string
  category: string
  content: string | Record<string, unknown>
  metadata: Record<string, unknown>
  seq: number
  created_at: string
}

export type OfficeEmployeeListResponse = {
  employeeIds: string[]
  employees: OfficeEmployeeSummary[]
}
export type OfficeEmployeeTemplatesResponse = { templates: OfficeEmployeeTemplateSummary[] }
export type OfficeSkillsResponse = { skills: OfficeSkillSummary[] }
export type OfficeModelsResponse = {
  models: OfficeModelSummary[]
  token_usage: { enabled: boolean }
}
export type OfficeEmployeeMutationResponse = { employee: OfficeEmployeeSummary }
export type OfficeEmployeeDeleteResponse = { deleted: OfficeEmployeeSummary }
export type OfficeGroupMessagesResponse = {
  messages: OfficeGroupMessage[]
  nextAfterSequence: number
}
export type OfficeConversationMessagesResponse = { messages: OfficeConversationMessage[] }
export type OfficeQueueResponse = { items: QueuedOfficeMessage[] }
export type OfficeQueueItemResponse = { item: QueuedOfficeMessage }
export type OfficeRunListResponse = { runs: OfficeRun[] }
export type OfficeRunEventsResponse = { events: OfficeRunEvent[]; nextAfterSeq: number | null }

function getBridge() {
  const bridge = window.officeDesktopBridge
  if (!bridge) throw new Error('Office Desktop Bridge is unavailable')
  return bridge
}

export type OfficeWorkflow = {
  workflow_id: string
  task_plan: Array<{ task_id: string; employee_id: string; task: string; status: string; kind: string; run_id: string | null }>
  quality: Record<string, { status: string; summary: string; blockingIssues: string[]; sourceRunId: string; evidence: Array<{ toolCallId: string }> }>
}
export type OfficeGroupExecution = {
  activeCount: number; paused: boolean; stopping: boolean; heldQueueIds: string[]; error: string
  runTargets: Array<{ employeeId: string; runId: string; projectId: string | null; workId: string | null }>
}
export type OfficeWorkflowResponse = { workflow: OfficeWorkflow | null }
export type OfficeGroupResetResult = {
  clearedMessages: number
  clearedThreads: string[]
  clearedWorkflows: number
  droppedQueueItems: string[]
  cancelledWorkflowId: string | null
}

const employees = new EmployeeProjection(
  async () => (await getBridge().listOfficeEmployees()).employees,
  next => window.dispatchEvent(new CustomEvent('office-employees-updated', { detail: next })),
)

async function employeeMutation<T>(action: () => Promise<T>): Promise<T> {
  try { return await action() }
  finally { employees.invalidate() }
}

export const officeApi = {
  listManagedModels: () => getBridge().listOfficeModels(),
  addModel: (payload: OfficeAddModelInput) => getBridge().addOfficeModel(payload),
  getModel: (modelId: string) => getBridge().getOfficeModel(modelId),
  updateModel: (modelId: string, payload: OfficeEditModelInput) => employeeMutation(() => getBridge().updateOfficeModel(modelId, payload)),
  setDefaultModel: (modelId: string) => employeeMutation(() => getBridge().setOfficeDefaultModel(modelId)),
  getGroupExecution: () => getBridge().getOfficeGroupExecution(),
  pauseGroup: () => employeeMutation(() => getBridge().pauseOfficeGroup()),
  resetGroup: () => employeeMutation(() => getBridge().resetOfficeGroup()),
  async getCurrentWorkflow() { return (await getBridge().getCurrentOfficeWorkflow()).workflow },
  async getWorkflow(id: string) { return (await getBridge().getOfficeWorkflow(id)).workflow },
  openExternalUrl(url: string) {
    return getBridge().openExternalUrl(url)
  },
  previewFile(payload: OfficeFileReference & { metadataOnly?: boolean }) {
    return getBridge().previewOfficeFile(payload)
  },
  revealFile(payload: OfficeFileReference) {
    return getBridge().revealOfficeFile(payload)
  },
  invalidateEmployees: () => employees.invalidate(),
  listEmployees(options: { fresh?: boolean } = {}) {
    return employees.read(options.fresh)
  },
  async getEmployee(employeeId: string) {
    return getBridge().getOfficeEmployee(employeeId)
  },
  async getEmployeePersonalInfo(employeeId: string) {
    return (await this.getEmployee(employeeId)).personalInfo
  },
  async listEmployeeTemplates() {
    return (await getBridge().listOfficeEmployeeTemplates()).templates
  },
  async listModels() {
    return (await getBridge().listModels()).models
  },
  async listSkills() {
    return (await getBridge().listSkills()).skills
  },
  getBootstrap() {
    return getBridge().getOfficeBootstrap()
  },
  getSettings() {
    return getBridge().getOfficeSettings()
  },
  listExecutionApprovals() {
    return getBridge().listExecutionApprovals()
  },
  decideExecutionApproval(id: string, hash: string, decision: 'approve' | 'reject') {
    return getBridge().decideExecutionApproval(id, hash, decision)
  },
  listMcpServices() { return getBridge().listMcpServices() },
  changeMcpService(name: string, change: import('./mcp').McpChange) { return getBridge().changeMcpService(name, change) },
  getEmployeeTools(employeeId: string) {
    return getBridge().getOfficeEmployeeTools(employeeId)
  },
  saveEmployeeTools(employeeId: string, tools: string[], expectedRevision: string) {
    return employeeMutation(() => getBridge().saveOfficeEmployeeTools(employeeId, { tools, expectedRevision }))
  },
  saveSettings(payload: OfficeSettingsInput) {
    return getBridge().saveOfficeSettings(payload)
  },
  getUserMemory() {
    return getBridge().getUserMemory()
  },
  listWorkLogDays(employeeId: string, before?: string) {
    return getBridge().listWorkLogDays(employeeId, before)
  },
  getWorkLogDay(employeeId: string, day: string) {
    return getBridge().getWorkLogDay(employeeId, day)
  },
  getEmployeeMemory(employeeId: string) {
    return getBridge().getEmployeeMemory(employeeId)
  },
  createMemoryFact(payload: OfficeMemoryFactInput) {
    return getBridge().createMemoryFact(payload)
  },
  updateMemoryFact(factId: string, payload: Partial<OfficeMemoryFactInput>) {
    return getBridge().updateMemoryFact(factId, payload)
  },
  deleteMemoryFact(factId: string) {
    return getBridge().deleteMemoryFact(factId)
  },
  testBootstrapModel(payload: OfficeBootstrapInput) {
    return getBridge().testOfficeBootstrapModel(payload)
  },
  initializeBootstrap(payload: OfficeBootstrapInput) {
    return employeeMutation(() => getBridge().initializeOfficeBootstrap(payload))
  },
  async createEmployee(payload: OfficeCreateEmployeeInput) {
    return (await employeeMutation(() => getBridge().createOfficeEmployee(payload))).employee
  },
  async saveEmployeePersonalInfo(employeeId: string, payload: OfficeEmployeePersonalInfoInput) {
    return (await employeeMutation(() => getBridge().saveOfficeEmployeePersonalInfo(employeeId, payload))).employee
  },
  async deleteEmployee(employeeId: string) {
    return (await employeeMutation(() => getBridge().deleteOfficeEmployee(employeeId))).deleted
  },
  sendMessage(command: OfficeMessageCommand) {
    return employeeMutation(() => getBridge().sendOfficeMessage(command))
  },
  listGroupMessages(afterSequence?: number) {
    return getBridge().listOfficeGroupMessages(afterSequence)
  },
  async listConversationMessages(employeeId: string, kind: OfficeConversationKind) {
    return (await getBridge().listOfficeConversationMessages(employeeId, kind)).messages
  },
  async listQueue() {
    return (await getBridge().listOfficeQueue()).items
  },
  async editQueue(queueId: string, text: string) {
    return (await getBridge().editOfficeQueue(queueId, text)).item
  },
  async deleteQueue(queueId: string) {
    return (await getBridge().deleteOfficeQueue(queueId)).item
  },
  cancelRun(employeeId: string, runId: string) {
    return employeeMutation(() => getBridge().cancelOfficeRun(employeeId, runId))
  },
  resumeRun(employeeId: string, runId: string, payload: OfficeRunResumeInput) {
    return employeeMutation(() => getBridge().resumeOfficeRun(employeeId, runId, payload))
  },
  getRun(employeeId: string, runId: string) {
    return getBridge().getOfficeRun(employeeId, runId)
  },
  async listRuns(employeeId: string, kind: OfficeConversationKind, limit?: number) {
    return (await getBridge().listOfficeRuns(employeeId, kind, limit)).runs
  },
  listRunEvents(employeeId: string, runId: string, afterSeq?: number) {
    return getBridge().listOfficeRunEvents(employeeId, runId, afterSeq)
  },
}
