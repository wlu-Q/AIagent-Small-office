import { t, useLocale } from '../../i18n'
import React, { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { formatWorkDuration } from './chatHistory'

const Progress = styled.section`
  margin: 14px 0 20px;
  min-width: 0;
  > header {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #808a96;
    font-size: 13px;
  }
  > header::after { content: ''; height: 1px; background: #edf0f2; flex: 1; }
  > header > button {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border: 0;
    background: transparent;
    color: inherit;
    padding: 7px 0;
    font: inherit;
    cursor: pointer;
    border-radius: 4px;
  }
  > header > button:hover { color: #415b6b; }
  > header > button:focus-visible { outline: 2px solid #77b8b1; outline-offset: 4px; }
  [data-work-chevron] { display: inline-block; transition: transform 160ms ease; }
  button[aria-expanded='true'] [data-work-chevron] { transform: rotate(90deg); }
  [data-work-content] { padding-top: 14px; }
  @media (prefers-reduced-motion: reduce) { [data-work-chevron] { transition: none; } }
`

export default function WorkProgress({ workKey, startedAt, finishedAt, active, waiting, status, hasSteps, children }: {
  workKey: string; startedAt?: number; finishedAt?: number; active: boolean; waiting: boolean
  status: string; hasSteps: boolean; children: React.ReactNode
}) {
  useLocale()
  const [manualOpen, setManualOpen] = useState<boolean | null>(null)
  const [now, setNow] = useState(Date.now)
  const expanded = manualOpen ?? active
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  const end = active ? now : finishedAt
  const duration = startedAt !== undefined && end !== undefined && Number.isFinite(end)
    ? formatWorkDuration(end - startedAt) : null
  const state = waiting ? t('ui.awaiting_reply') : active ? t('ui.in_progress') : status === 'error' ? t('ui.execution_failed')
    : status === 'timeout' ? t('ui.timed_out') : status === 'interrupted' ? t('ui.stopped') : ''
  const label = [state, duration ? t(active ? 'progress.elapsed' : 'progress.duration', { duration }) : t('ui.execution_details')].filter(Boolean).join(' · ')
  const contentId = `work-progress-${workKey.replace(/[^a-z0-9_-]/gi, '-')}`
  return <Progress data-office-work-progress={workKey}>
    <header>{hasSteps ? <button type="button" aria-expanded={expanded} aria-controls={contentId}
      onClick={() => setManualOpen(!expanded)}>{label}<span data-work-chevron aria-hidden="true">›</span></button>
      : <span style={{ padding: '7px 0' }}>{label}</span>}</header>
    {hasSteps && <div id={contentId} data-work-content hidden={!expanded}>{children}</div>}
  </Progress>
}
