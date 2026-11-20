import { create } from 'zustand'
import type { WorkstationSnapshot } from './workstationModel'

export const useWorkstationState = create<{
  active: WorkstationSnapshot | null
  open: (snapshot: WorkstationSnapshot) => void
  close: () => void
  sync: (snapshot: WorkstationSnapshot) => void
}>((set, get) => ({
  active: null,
  open: active => set({ active }),
  close: () => set({ active: null }),
  sync: active => {
    const previous = get().active
    if (!previous || previous.workstationId !== active.workstationId) return
    if (previous.employeeId !== active.employeeId || previous.displayName !== active.displayName ||
      previous.characterTexture !== active.characterTexture || previous.computerFrame !== active.computerFrame ||
      previous.state !== active.state || previous.atDesk !== active.atDesk) set({ active })
  },
}))
