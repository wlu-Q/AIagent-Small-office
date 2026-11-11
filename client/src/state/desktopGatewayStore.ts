import { create } from 'zustand'

import { createGatewayStatus } from '../services/desktop/gatewayStatusDetails'
import type { DesktopGatewayStatus } from '../services/desktop/types'

const browserStatus: DesktopGatewayStatus = createGatewayStatus({
  mode: 'browser',
  lifecycle: 'browser',
  transport: 'none',
})

type DesktopGatewayState = {
  status: DesktopGatewayStatus
  setStatus: (status: DesktopGatewayStatus) => void
}

export const useDesktopGatewayStore = create<DesktopGatewayState>((set) => ({
  status: browserStatus,
  setStatus: (status) => set({ status }),
}))

export function getBrowserGatewayStatus() {
  return {
    ...browserStatus,
    lastUpdatedAt: Date.now(),
  }
}
