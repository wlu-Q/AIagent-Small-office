import { t } from '../../i18n'
import gatewayStatusDetails from '../../../../contracts/desktop-gateway-status.json'
import type { DesktopGatewayLifecycle, DesktopGatewayStatus } from './types'

type StaticGatewayStatusDetail = Record<DesktopGatewayLifecycle, string> & {
  launching: string
  startupFailed: string
}

const details = gatewayStatusDetails as StaticGatewayStatusDetail

export function getGatewayStatusDetail(lifecycle: DesktopGatewayLifecycle) {
  return t(details[lifecycle])
}

export function createGatewayStatus(
  patch: Omit<Partial<DesktopGatewayStatus>, 'lastUpdatedAt'> & {
    mode: DesktopGatewayStatus['mode']
    lifecycle: DesktopGatewayLifecycle
    transport: DesktopGatewayStatus['transport']
    detail?: string
  },
): DesktopGatewayStatus {
  return {
    ...patch,
    detail: patch.detail ?? getGatewayStatusDetail(patch.lifecycle),
    lastUpdatedAt: Date.now(),
  }
}

export function normalizeGatewayStatus(status: DesktopGatewayStatus): DesktopGatewayStatus {
  return {
    ...status,
    detail: status.detail || getGatewayStatusDetail(status.lifecycle),
    lastUpdatedAt: status.lastUpdatedAt || Date.now(),
  }
}

export function getGatewayLaunchDetail() {
  return t(details.launching)
}

export function getGatewayStartupFailedDetail() {
  return t(details.startupFailed)
}
