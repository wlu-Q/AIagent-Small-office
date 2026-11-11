import { t } from '../../i18n'
import { useWorkspaceStore } from '../../state/workspaceStore'
import type {
  DesktopBridgeApi,
  DesktopBridgeEvent,
  DesktopGatewayLifecycle,
  WorkspaceSummary,
} from './types'

type WorkspaceSummaryListener = (summary: WorkspaceSummary | undefined) => void

let initialized = false
let unsubscribe: (() => void) | undefined
let refreshRequestId = 0
let lastServiceLifecycle: DesktopGatewayLifecycle | undefined
const workspaceSummaryListeners = new Set<WorkspaceSummaryListener>()

function getBridge(): DesktopBridgeApi | undefined {
  return window.officeDesktopBridge
}

function publishWorkspaceSummary(summary: WorkspaceSummary | undefined) {
  for (const listener of workspaceSummaryListeners) {
    listener(summary)
  }
}

function applyAuthoritativeWorkspace(summary?: WorkspaceSummary) {
  if (!summary) {
    useWorkspaceStore.getState().setIdle(t('ui.no_local_project_is_bound'))
    publishWorkspaceSummary(undefined)
    return
  }

  useWorkspaceStore.getState().setReady(`\u5f53\u524d\u9879\u76ee\uff1a${summary.name}`)
  publishWorkspaceSummary(summary)
}

export function subscribeWorkspaceSummary(listener: WorkspaceSummaryListener) {
  workspaceSummaryListeners.add(listener)
  return () => {
    workspaceSummaryListeners.delete(listener)
  }
}

export async function refreshWorkspaceSummary(): Promise<WorkspaceSummary | undefined> {
  const bridge = getBridge()
  if (!bridge) {
    useWorkspaceStore.getState().setIdle(t('ui.local_project_binding_is_unavailable_in_browser_mode'))
    publishWorkspaceSummary(undefined)
    return undefined
  }

  const requestId = ++refreshRequestId
  const result = await bridge.getWorkspaceSummary()

  // Only the latest backend read may update the renderer projection. IPC
  // workspace.updated is an invalidation hint; data is always re-read from the backend.
  if (requestId !== refreshRequestId) {
    return undefined
  }

  applyAuthoritativeWorkspace(result.workspace)
  return result.workspace
}

function reportRefreshError(error: unknown) {
  useWorkspaceStore
    .getState()
    .setError(error instanceof Error ? error.message : t('ui.unable_to_load_current_project_status_2'))
}

export function initializeWorkspaceBridge() {
  if (initialized) {
    return () => {
      unsubscribe?.()
      unsubscribe = undefined
      initialized = false
      lastServiceLifecycle = undefined
    }
  }

  initialized = true
  const bridge = getBridge()

  if (!bridge) {
    useWorkspaceStore.getState().setIdle(t('ui.local_project_binding_is_unavailable_in_browser_mode'))
    publishWorkspaceSummary(undefined)
    return () => {
      initialized = false
      lastServiceLifecycle = undefined
    }
  }

  unsubscribe = bridge.onServiceEvent((event: DesktopBridgeEvent) => {
    if (event.type === 'workspace.updated') {
      void refreshWorkspaceSummary().catch(reportRefreshError)
      return
    }

    const previousLifecycle = lastServiceLifecycle
    lastServiceLifecycle = event.payload.lifecycle
    if (event.payload.lifecycle === 'connected' && previousLifecycle !== 'connected') {
      void refreshWorkspaceSummary().catch(reportRefreshError)
    }
  })

  void refreshWorkspaceSummary().catch(reportRefreshError)

  return () => {
    unsubscribe?.()
    unsubscribe = undefined
    initialized = false
    lastServiceLifecycle = undefined
  }
}

export async function selectAndBindWorkspace(): Promise<WorkspaceSummary | undefined> {
  const bridge = getBridge()
  if (!bridge) {
    const detail = t('ui.cannot_bind_a_local_project_in_browser_mode')
    useWorkspaceStore.getState().setError(detail)
    throw new Error(detail)
  }

  if (typeof bridge.selectAndBindWorkspace !== 'function') {
    const detail = t('ui.desktop_runtime_is_outdated_quit_and_restart_small_office_before_binding_a')
    useWorkspaceStore.getState().setError(detail)
    throw new Error(detail)
  }

  useWorkspaceStore.getState().setBinding(t('ui.selecting_and_reading_project_folder'))

  try {
    const result = await bridge.selectAndBindWorkspace()
    if (result.canceled) {
      await refreshWorkspaceSummary()
      return undefined
    }

    const workspace = await refreshWorkspaceSummary()
    if (!workspace) {
      throw new Error(t('ui.desktop_did_not_return_the_bound_project'))
    }

    return workspace
  } catch (error) {
    const detail = error instanceof Error ? error.message : t('ui.project_binding_failed')
    useWorkspaceStore.getState().setError(detail)
    throw error
  }
}
