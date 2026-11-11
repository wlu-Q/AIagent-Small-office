import { t, useLocale, roleLabel } from '../../i18n'
import React from 'react'
import styled from '@emotion/styled'
import type { OfficeEmployeeSummary } from '../../services/office/officeApi'

type Selection = { kind: 'group' }
  | { kind: 'private'; employeeId: string }

const Sidebar = styled.aside`
  width: 286px;
  flex: 0 0 286px;
  min-height: 0;
  overflow: auto;
  border-right: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(241, 245, 249, 0.96);
  display: flex;
  flex-direction: column;

  @media (max-width: 760px) {
    width: 210px;
    flex-basis: 210px;
  }
`

const SidebarTitle = styled.div`
  padding: 18px 18px 10px;
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
`

const ConversationList = styled.div`
  min-height: 0;
  overflow-y: auto;
  padding: 0 10px 12px;
`

const ConversationItem = styled.button<{ $selected?: boolean }>`
  min-height: 64px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 8px 10px;
  border: 0;
  border-radius: 12px;
  background: ${(props) => (props.$selected ? 'rgba(219, 234, 254, 0.96)' : 'transparent')};
  color: #0f172a;
  text-align: left;
  cursor: pointer;
  font: inherit;

  & + & {
    margin-top: 3px;
  }

  &:hover {
    background: ${(props) => (props.$selected ? 'rgba(219, 234, 254, 0.96)' : 'rgba(226, 232, 240, 0.72)')};
  }
`

const GroupAvatar = styled.div`
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: linear-gradient(145deg, #38bdf8, #2563eb);
  color: #ffffff;
  box-shadow: 0 7px 16px rgba(37, 99, 235, 0.24);
  font-size: 22px;
`

const EmployeeAvatar = styled(GroupAvatar)`
  background: linear-gradient(145deg, #58b6a9, #2d8b85);
  box-shadow: 0 7px 16px rgba(45, 139, 133, 0.2);
  font-size: 17px;
`

const ConversationMeta = styled.div`
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;

  strong,
  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: #0f172a;
    font-size: 14px;
    font-weight: 650;
  }

  span {
    color: #64748b;
    font-size: 12px;
  }
`

const EmptyEmployeeList = styled.div`
  margin: 4px 10px 0;
  padding: 18px 12px;
  border: 1px dashed rgba(148, 163, 184, 0.32);
  border-radius: 12px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
`

type Props = {
  employees: OfficeEmployeeSummary[]
  selection?: Selection
  onSelect: (selection: Selection) => void
}

export type { Selection }

export default function ChatSidebar({ employees, selection = { kind: 'group' }, onSelect }: Props) {
  useLocale()
  const privateEmployees = employees.filter((employee) => !employee.isPm)

  return (
    <Sidebar aria-label={t('ui.office_conversations')} data-office-chat-sidebar>
      <SidebarTitle>{t('ui.conversations')}</SidebarTitle>
      <ConversationList>
        <ConversationItem
          type="button"
          $selected={selection.kind === 'group'}
          data-office-group
          onClick={() => onSelect({ kind: 'group' })}
        >
          <GroupAvatar aria-hidden="true">{t('ui.group').slice(0, 1)}</GroupAvatar>
          <ConversationMeta>
            <strong>{t('ui.office_group_chat')}</strong>
            <span>{t('ui.all_employees')}</span>
          </ConversationMeta>
        </ConversationItem>
      </ConversationList>
      <SidebarTitle>{t('ui.direct_messages')}</SidebarTitle>
      {privateEmployees.length === 0 ? (
        <EmptyEmployeeList data-office-employee-list>{t('ui.no_employees_available_for_direct_messages')}</EmptyEmployeeList>
      ) : (
        <ConversationList data-office-employee-list>
          {privateEmployees.map((employee) => (
            <ConversationItem
              key={employee.id}
              type="button"
              $selected={selection.kind === 'private' && selection.employeeId === employee.id}
              data-office-employee={employee.id}
              onClick={() => onSelect({ kind: 'private', employeeId: employee.id })}
            >
              <EmployeeAvatar aria-hidden="true">{employee.displayName.slice(0, 1)}</EmployeeAvatar>
              <ConversationMeta>
                <strong>{employee.displayName}</strong>
                <span>{employee.role?.trim() ? `${roleLabel(employee)} · ` : ''}{employee.state === 'working' ? t('ui.working_2') : t('ui.direct_message')}</span>
              </ConversationMeta>
            </ConversationItem>
          ))}
        </ConversationList>
      )}
    </Sidebar>
  )
}
