import { getBrowserGatewayStatus, useDesktopGatewayStore } from '../../state/desktopGatewayStore'
import { createGatewayStatus, getGatewayStatusDetail, normalizeGatewayStatus } from './gatewayStatusDetails'
import type { DesktopBridgeApi, DesktopBridgeEvent, DesktopGatewayStatus } from './types'

let initialized = false
let unsubscribe: (() => void) | undefined

function getBridge(): DesktopBridgeApi | undefined {
  return window.officeDesktopBridge
}

function applyStatus(status: DesktopGatewayStatus) {
  useDesktopGatewayStore.getState().setStatus(normalizeGatewayStatus(status))
}

export function initializeDesktopBridge() {
  if (initialized) {
    return () => {
      unsubscribe?.()
      unsubscribe = undefined
      initialized = false
    }
  }

  initialized = true
  const bridge = getBridge()

  if (!bridge) {
    applyStatus(getBrowserGatewayStatus())
    return () => {
      initialized = false
    }
  }

  applyStatus(createGatewayStatus({
    mode: 'electron',
    lifecycle: 'starting',
    transport: 'http',
  }))

  bridge
    .getServiceStatus()
    .then((status) => {
      applyStatus(status)
    })
    .catch((error) => {
      applyStatus({
        mode: 'electron',
        lifecycle: 'error',
        detail: error instanceof Error ? error.message : getGatewayStatusDetail('error'),
        transport: 'http',
        lastUpdatedAt: Date.now(),
      })
    })

  unsubscribe = bridge.onServiceEvent((event: DesktopBridgeEvent) => {
    if (event.type === 'service.status') {
      applyStatus(event.payload)
    }
  })

  bridge.pingService().catch(() => {
    // Event stream remains the source of truth.
  })

  return () => {
    unsubscribe?.()
    unsubscribe = undefined
    initialized = false
  }
}
