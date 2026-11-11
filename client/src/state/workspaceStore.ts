import { t } from '../i18n'
import { create } from 'zustand'

export type WorkspaceBindingState = 'browser' | 'idle' | 'binding' | 'ready' | 'error'

function hasDesktopBridge() {
  return typeof window !== 'undefined' && Boolean(window.officeDesktopBridge)
}

type WorkspaceState = {
  status: WorkspaceBindingState
  detail: string
  setIdle: (detail?: string) => void
  setBinding: (detail?: string) => void
  setReady: (detail: string) => void
  setError: (detail: string) => void
}

const browserDetail = () => t('ui.local_project_binding_is_unavailable_in_browser_mode')
const unboundDetail = () => t('ui.no_local_project_is_bound')

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  status: hasDesktopBridge() ? 'idle' : 'browser',
  detail: hasDesktopBridge() ? unboundDetail() : browserDetail(),
  setIdle: (detail) =>
    set({
      status: hasDesktopBridge() ? 'idle' : 'browser',
      detail: hasDesktopBridge() ? detail ?? unboundDetail() : browserDetail(),
    }),
  setBinding: (detail) =>
    set({
      status: 'binding',
      detail: detail ?? t('ui.loading_project_binding'),
    }),
  setReady: (detail) =>
    set({
      status: 'ready',
      detail,
    }),
  setError: (detail) =>
    set({
      status: 'error',
      detail,
    }),
}))
