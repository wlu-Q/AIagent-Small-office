import { t, useLocale } from '../i18n'
import React, { useEffect } from 'react'
import styled from '@emotion/styled'

import { initializeDesktopBridge } from '../services/desktop/desktopBridge'
import type { DesktopGatewayLifecycle } from '../services/desktop/types'
import { useDesktopGatewayStore } from '../state/desktopGatewayStore'

const Wrapper = styled.div<{ $tone: string }>`
  position: fixed;
  left: 16px;
  top: 18px;
  z-index: 11990;
  pointer-events: none;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.22);
  background: ${(props) => props.$tone};
  color: #eef2ff;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.18);
  display: inline-flex;
  align-items: center;
  gap: 10px;
  max-width: min(420px, calc(100vw - 48px));
`

const Dot = styled.span<{ $tone: string }>`
  width: 10px;
  height: 10px;
  border-radius: 999px;
  flex-shrink: 0;
  background: ${(props) => props.$tone};
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.12);
`

const TextGroup = styled.div`
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
`

const Title = styled.span`
  font-size: 12px;
  font-weight: 700;
  line-height: 1.2;
`

const Detail = styled.span`
  font-size: 11px;
  color: rgba(226, 232, 240, 0.88);
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`

function getBadgeStyle(lifecycle: DesktopGatewayLifecycle) {
  switch (lifecycle) {
    case 'connected':
      return {
        background: 'linear-gradient(180deg, rgba(15, 118, 110, 0.96), rgba(15, 76, 96, 0.96))',
        dot: '#5eead4',
        title: t('ui.server_connected'),
      }
    case 'starting':
      return {
        background: 'linear-gradient(180deg, rgba(59, 130, 246, 0.96), rgba(29, 78, 216, 0.96))',
        dot: '#bfdbfe',
        title: t('ui.connecting_to_server'),
      }
    case 'unavailable':
      return {
        background: 'linear-gradient(180deg, rgba(217, 119, 6, 0.96), rgba(180, 83, 9, 0.96))',
        dot: '#fde68a',
        title: t('ui.server_unavailable'),
      }
    case 'error':
      return {
        background: 'linear-gradient(180deg, rgba(185, 28, 28, 0.96), rgba(127, 29, 29, 0.96))',
        dot: '#fecaca',
        title: t('ui.server_error'),
      }
    default:
      return {
        background: 'linear-gradient(180deg, rgba(71, 85, 105, 0.96), rgba(30, 41, 59, 0.96))',
        dot: '#cbd5e1',
        title: t('ui.browser_mode'),
      }
  }
}

export default function DesktopGatewayBadge() {
  useLocale()
  const status = useDesktopGatewayStore((state) => state.status)

  useEffect(() => initializeDesktopBridge(), [])

  const style = getBadgeStyle(status.lifecycle)
  // 连接成功时标题已说明一切，不再重复一行副标题
  const showDetail = status.lifecycle !== 'connected'

  return (
    <Wrapper $tone={style.background}>
      <Dot $tone={style.dot} />
      <TextGroup>
        <Title>{style.title}</Title>
        {showDetail ? <Detail>{status.detail}</Detail> : null}
      </TextGroup>
    </Wrapper>
  )
}
