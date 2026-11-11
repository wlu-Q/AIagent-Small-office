export const OPEN_EMPLOYEE_CHAT_EVENT = 'office-chat:open-employee'

export function openEmployeeChat(employeeId: string) {
  window.dispatchEvent(new CustomEvent<string>(OPEN_EMPLOYEE_CHAT_EVENT, { detail: employeeId }))
}
