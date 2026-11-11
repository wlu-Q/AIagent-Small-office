import type { LibraryRequest } from '../../components/office-library/libraryApi'
import type {
  OfficeWorkflowResponse,
  OfficeConversationKind,
  OfficeConversationMessagesResponse,
  OfficeCreateEmployeeInput,
  OfficeEmployeeDeleteResponse,
  OfficeEmployeeListResponse,
  OfficeEmployeeMutationResponse,
  OfficeEmployeePersonalInfoInput,
  OfficeEmployeeDetail,
  OfficeEmployeeTemplatesResponse,
  OfficeGroupMessagesResponse,
  OfficeMessageCommand,
  OfficeMessageResult,
  OfficeModelsResponse,
  OfficeQueueResponse,
  OfficeQueueItemResponse,
  OfficeRunResumeInput,
  OfficeRunResumeResult,
  OfficeRun,
  OfficeRunListResponse,
  OfficeRunEventsResponse,
  OfficeSkillsResponse,
  OfficeBootstrapInput,
  OfficeBootstrapModel,
  OfficeBootstrapStatus,
  OfficeMemoryFactInput,
  OfficeMemoryResponse,
  OfficeSettingsInput,
  OfficeModelCatalogue,
  OfficeAddModelInput,
  OfficeEditModelInput,
  OfficeModelDetail,
  OfficeFileReference,
  OfficeFilePreview,
} from '../office/officeApi'

export type DesktopGatewayLifecycle =
  | 'browser'
  | 'starting'
  | 'connected'
  | 'unavailable'
  | 'error'

export type DesktopGatewayStatus = {
  mode: 'browser' | 'electron'
  lifecycle: DesktopGatewayLifecycle
  detail: string
  transport: 'none' | 'http'
  lastUpdatedAt: number
}

export type WorkspaceKind = 'empty' | 'existing'

export type WorkspaceTreeNode = {
  name: string
  type: 'directory' | 'file' | 'more'
  children?: WorkspaceTreeNode[]
}

export type WorkspaceSummary = {
  projectId?: string
  path: string
  displayPath: string | null
  name: string
  kind: WorkspaceKind
  tree: WorkspaceTreeNode[]
}

export type DesktopBridgeEvent =
  | { type: 'service.status'; payload: DesktopGatewayStatus }
  | { type: 'workspace.updated' }

export type DesktopBridgeApi = {
  listExecutionApprovals: () => Promise<{ items: import('../office/officeApi').OfficeExecutionApproval[] }>
  decideExecutionApproval: (id: string, hash: string, decision: 'approve' | 'reject') => Promise<unknown>
  getOfficeEmployeeTools: (employeeId: string) => Promise<import('../office/officeApi').OfficeEmployeeTools>
  saveOfficeEmployeeTools: (employeeId: string, payload: { tools: string[]; expectedRevision: string }) => Promise<import('../office/officeApi').OfficeEmployeeTools>
  officeLibrary: (payload: LibraryRequest) => Promise<unknown>
  openExternalUrl: (url: string) => Promise<void>
  previewOfficeFile: (payload: OfficeFileReference & { metadataOnly?: boolean }) => Promise<OfficeFilePreview>
  revealOfficeFile: (payload: OfficeFileReference) => Promise<{ path: string }>
  getServiceStatus: () => Promise<DesktopGatewayStatus>
  pingService: () => Promise<{ pong: boolean; timestamp: number }>
  getWorkspaceSummary: () => Promise<{ workspace?: WorkspaceSummary }>
  selectAndBindWorkspace: () => Promise<{ canceled: boolean; workspace?: WorkspaceSummary }>
  listOfficeEmployees: () => Promise<OfficeEmployeeListResponse>
  getOfficeEmployee: (employeeId: string) => Promise<OfficeEmployeeDetail>
  listOfficeEmployeeTemplates: () => Promise<OfficeEmployeeTemplatesResponse>
  listModels: () => Promise<OfficeModelsResponse>
  listOfficeModels: () => Promise<OfficeModelCatalogue>
  addOfficeModel: (payload: OfficeAddModelInput) => Promise<OfficeModelCatalogue>
  getOfficeModel: (modelId: string) => Promise<OfficeModelDetail>
  updateOfficeModel: (modelId: string, payload: OfficeEditModelInput) => Promise<OfficeModelCatalogue>
  setOfficeDefaultModel: (modelId: string) => Promise<OfficeModelCatalogue>
  listSkills: () => Promise<OfficeSkillsResponse>
  getOfficeBootstrap: () => Promise<OfficeBootstrapStatus>
  getOfficeSettings: () => Promise<OfficeBootstrapStatus>
  getLanguage: () => Promise<'en' | 'zh-CN'>
  setLanguage: (language: 'en' | 'zh-CN') => Promise<'en' | 'zh-CN'>
  saveOfficeSettings: (payload: OfficeSettingsInput) => Promise<OfficeBootstrapStatus>
  getUserMemory: () => Promise<OfficeMemoryResponse>
  createMemoryFact: (payload: OfficeMemoryFactInput) => Promise<OfficeMemoryResponse>
  updateMemoryFact: (factId: string, payload: Partial<OfficeMemoryFactInput>) => Promise<OfficeMemoryResponse>
  deleteMemoryFact: (factId: string) => Promise<OfficeMemoryResponse>
  testOfficeBootstrapModel: (payload: OfficeBootstrapInput) => Promise<OfficeBootstrapModel>
  initializeOfficeBootstrap: (payload: OfficeBootstrapInput) => Promise<OfficeBootstrapStatus>
  createOfficeEmployee: (payload: OfficeCreateEmployeeInput) => Promise<OfficeEmployeeMutationResponse>
  saveOfficeEmployeePersonalInfo: (
    employeeId: string,
    payload: OfficeEmployeePersonalInfoInput,
  ) => Promise<OfficeEmployeeMutationResponse>
  deleteOfficeEmployee: (employeeId: string) => Promise<OfficeEmployeeDeleteResponse>
  sendOfficeMessage: (command: OfficeMessageCommand) => Promise<OfficeMessageResult>
  listOfficeGroupMessages: (afterSequence?: number) => Promise<OfficeGroupMessagesResponse>
  listOfficeConversationMessages: (
    employeeId: string,
    kind: OfficeConversationKind,
  ) => Promise<OfficeConversationMessagesResponse>
  listOfficeQueue: () => Promise<OfficeQueueResponse>
  editOfficeQueue: (queueId: string, text: string) => Promise<OfficeQueueItemResponse>
  deleteOfficeQueue: (queueId: string) => Promise<OfficeQueueItemResponse>
  getCurrentOfficeWorkflow: () => Promise<OfficeWorkflowResponse>
  getOfficeWorkflow: (workflowId: string) => Promise<OfficeWorkflowResponse>
  getOfficeGroupExecution: () => Promise<import('../office/officeApi').OfficeGroupExecution>
  pauseOfficeGroup: () => Promise<import('../office/officeApi').OfficeGroupExecution>
  resetOfficeGroup: () => Promise<import('../office/officeApi').OfficeGroupResetResult>
  cancelOfficeRun: (employeeId: string, runId: string) => Promise<OfficeRun>
  resumeOfficeRun: (
    employeeId: string,
    runId: string,
    payload: OfficeRunResumeInput,
  ) => Promise<OfficeRunResumeResult>
  getOfficeRun: (employeeId: string, runId: string) => Promise<OfficeRun>
  listOfficeRuns: (
    employeeId: string,
    kind: OfficeConversationKind,
    limit?: number,
  ) => Promise<OfficeRunListResponse>
  listOfficeRunEvents: (
    employeeId: string,
    runId: string,
    afterSeq?: number,
  ) => Promise<OfficeRunEventsResponse>
  getZoomFactor: () => Promise<number>
  setZoomFactor: (factor: number) => Promise<number>
  zoomBy: (delta: number) => Promise<number>
  resetZoom: () => Promise<number>
  onServiceEvent: (listener: (event: DesktopBridgeEvent) => void) => () => void
}

declare global {
  interface Window {
    officeDesktopBridge?: DesktopBridgeApi
  }
}
