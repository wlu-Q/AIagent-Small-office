import { t, useLocale } from '../i18n'
import React, { useState } from 'react'
import styled from '@emotion/styled'

import type { OfficeEmployeeSummary, OfficeRun, QueuedOfficeMessage } from '../services/office/officeApi'

const Panel = styled.section`
  border-bottom: 1px solid #dce3ed;
  background: #f8fafc;
  padding: 8px 10px;
`

const Row = styled.div`
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px;
  align-items: center;
  padding: 5px 0;
  font-size: 12px;
`

type Props = {
  employees: OfficeEmployeeSummary[]
  queue: QueuedOfficeMessage[]
  activeRuns: OfficeRun[]
  onEdit: (queueId: string, text: string) => Promise<void>
  onDelete: (queueId: string) => Promise<void>
  onCancel: (employeeId: string, runId: string) => Promise<void>
}

export default function OfficeQueuePanel({
  employees,
  queue,
  activeRuns,
  onEdit,
  onDelete,
  onCancel,
}: Props) {
  useLocale()
  const [editingId, setEditingId] = useState<string>()
  const [editingText, setEditingText] = useState('')
  const nameOf = (employeeId: string) => (
    employees.find((employee) => employee.id === employeeId)?.displayName || employeeId
  )

  if (queue.length === 0 && activeRuns.length === 0) return null

  return (
    <Panel aria-label={t('ui.task_queue')}>
      {activeRuns.map((run) => (
        <Row key={run.runId}>
          <span>{nameOf(run.employeeId)}{t('ui.currently_working')}</span>
          <button
            type="button"
            data-cancel-run-id={run.runId}
            onClick={() => void onCancel(run.employeeId, run.runId)}
          >
            {t('ui.interrupt')}</button>
        </Row>
      ))}
      {queue.map((item) => (
        <Row key={item.queueId} data-queue-id={item.queueId}>
          {editingId === item.queueId ? (
            <input
              data-queue-edit-input={item.queueId}
              value={editingText}
              onChange={(event) => setEditingText(event.target.value)}
            />
          ) : (
            <span>@{nameOf(item.targetEmployeeId)} {item.text}</span>
          )}
          <span>
            {editingId === item.queueId ? (
              <button
                type="button"
                data-save-queue={item.queueId}
                onClick={() => void onEdit(item.queueId, editingText).then(() => setEditingId(undefined))}
              >
                {t('ui.confirm')}</button>
            ) : (
              <button
                type="button"
                data-edit-queue={item.queueId}
                onClick={() => {
                  setEditingId(item.queueId)
                  setEditingText(item.text)
                }}
              >
                {t('ui.edit')}</button>
            )}
            <button
              type="button"
              data-delete-queue={item.queueId}
              onClick={() => void onDelete(item.queueId)}
            >
              {t('ui.delete')}</button>
          </span>
        </Row>
      ))}
    </Panel>
  )
}
