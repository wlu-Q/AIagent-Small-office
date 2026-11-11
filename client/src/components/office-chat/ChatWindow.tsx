import { t, useLocale } from '../../i18n'
import React from 'react'
import styled from '@emotion/styled'
import PhoneIphoneRoundedIcon from '@mui/icons-material/PhoneIphoneRounded'

const Launcher = styled.button`
  position: fixed;
  left: 16px;
  top: 84px;
  z-index: 22000;
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: #29a9e8;
  color: #ffffff;
  box-shadow: 0 10px 24px rgba(14, 116, 144, 0.28);
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease;

  svg {
    font-size: 27px;
  }

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 14px 30px rgba(14, 116, 144, 0.36);
  }

  &:active {
    transform: translateY(0);
  }
`

const Overlay = styled.div<{ $open: boolean }>`
  position: fixed;
  inset: 0;
  z-index: 21000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 24px 16px;
  background:
    radial-gradient(circle at top left, rgba(66, 109, 234, 0.18), transparent 30%),
    rgba(8, 11, 20, 0.54);
  pointer-events: ${(props) => (props.$open ? 'auto' : 'none')};
  opacity: ${(props) => (props.$open ? 1 : 0)};
  visibility: ${(props) => (props.$open ? 'visible' : 'hidden')};
  transition: opacity 180ms ease, visibility 180ms ease;
`

const WindowFrame = styled.section<{ $open: boolean }>`
  position: relative;
  width: min(73vw, calc(100vw - 32px));
  height: min(90vh, calc(100vh - 48px));
  min-width: 720px;
  min-height: 520px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(150, 168, 214, 0.34);
  border-radius: 20px;
  background: linear-gradient(180deg, rgba(249, 250, 255, 0.98), rgba(241, 244, 252, 0.98));
  color: #111827;
  box-shadow: 0 30px 72px rgba(0, 0, 0, 0.28);
  transform: ${(props) => (props.$open ? 'translateY(0) scale(1)' : 'translateY(-36px) scale(0.985)')};
  opacity: ${(props) => (props.$open ? 1 : 0)};
  transition: transform 260ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 180ms ease;

  @media (max-width: 760px) {
    width: calc(100vw - 24px);
    min-width: 0;
    height: calc(100vh - 32px);
    min-height: 0;
  }
`

const WindowBar = styled.header`
  height: 58px;
  flex: 0 0 58px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 68px 0 20px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.24);
  background: rgba(248, 250, 252, 0.98);
  user-select: none;

  svg {
    color: #2563eb;
  }
`

const WindowTitle = styled.h2`
  margin: 0;
  color: #0f172a;
  font-size: 16px;
  font-weight: 700;
`

const CloseButton = styled.button`
  position: absolute;
  top: 14px;
  right: 17px;
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 23px;
  line-height: 1;

  &:hover {
    background: #e2e8f0;
    color: #0f172a;
  }
`

const WindowBody = styled.div`
  min-height: 0;
  flex: 1 1 0;
  display: flex;
`

type Props = {
  approvalCount?: number
  open: boolean
  onToggle: () => void
  onClose: () => void
  sidebar: React.ReactNode
  children: React.ReactNode
}

export default function ChatWindow({ open, onToggle, onClose, sidebar, children, approvalCount = 0 }: Props) {
  useLocale()
  return (
    <>
      <Launcher
        type="button"
        aria-label={open ? t('ui.minimize_office_chat') : t('ui.open_office_chat')}
        aria-expanded={open}
        data-office-chat-launcher
        onClick={onToggle}
      >
        <PhoneIphoneRoundedIcon />
        {approvalCount > 0 && <span aria-label={t('ui.value_operations_awaiting_approval', { count: (approvalCount) })} style={{ position: 'absolute', right: -6, top: -5,
          background: '#bd562b', color: '#fff', borderRadius: 12, padding: '2px 6px', fontSize: 11 }}>{approvalCount}</span>}
      </Launcher>
      <Overlay $open={open} onMouseDown={onClose}>
        <WindowFrame
          $open={open}
          aria-label={t('ui.office_chat')}
          data-office-chat-window
          onMouseDown={(event) => event.stopPropagation()}
        >
          <WindowBar>
            <PhoneIphoneRoundedIcon />
            <WindowTitle>{t('ui.office_chat')}</WindowTitle>
          </WindowBar>
          <CloseButton type="button" aria-label={t('ui.close_office_chat')} onClick={onClose}>×</CloseButton>
          <WindowBody>
            {sidebar}
            {children}
          </WindowBody>
        </WindowFrame>
      </Overlay>
    </>
  )
}
