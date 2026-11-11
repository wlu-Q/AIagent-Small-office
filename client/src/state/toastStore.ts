import { create } from 'zustand'

export type ToastTone = 'success' | 'info' | 'warning' | 'error'

type ToastState = {
  open: boolean
  message: string
  tone: ToastTone
  showToast: (message: string, tone?: ToastTone) => void
  hideToast: () => void
}

export const useToastStore = create<ToastState>((set) => ({
  open: false,
  message: '',
  tone: 'info',
  showToast: (message, tone = 'info') =>
    set({
      open: true,
      message,
      tone,
    }),
  hideToast: () =>
    set({
      open: false,
    }),
}))

export function pushToast(message: string, tone: ToastTone = 'info') {
  useToastStore.getState().showToast(message, tone)
}
