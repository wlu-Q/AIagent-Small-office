import type { Selection } from './ChatSidebar'
import type { OfficeConversationMessage, OfficeGroupMessage, OfficeRun } from '../../services/office/officeApi'
import type { OfficeHumanInputChange, VisibleOfficeRunEvent } from '../../services/office/officeEvents'

export type OutgoingMessage = {
  messageId: string
  text: string
  createdAt: number
  status: 'sending' | 'accepted' | 'unconfirmed'
}

export const conversationKey = (selection: Selection) => selection.kind === 'group' ? 'group' : `private:${selection.employeeId}`

export class ConversationCache {
  private sessions = new Map<string, ReturnType<typeof emptySession>>()
  private requests = new Map<string, Promise<unknown>>()

  get(selection: Selection) {
    const key = conversationKey(selection)
    let session = this.sessions.get(key)
    if (!session) { session = emptySession(); this.sessions.set(key, session) }
    return session
  }

  request<T>(key: string, fetch: () => Promise<T>): Promise<T> {
    const existing = this.requests.get(key)
    if (existing) return existing as Promise<T>
    const pending = fetch().finally(() => { if (this.requests.get(key) === pending) this.requests.delete(key) })
    this.requests.set(key, pending)
    return pending
  }
}

function emptySession() {
  return {
    ready: false, messages: [] as OfficeConversationMessage[], groupMessages: [] as OfficeGroupMessage[],
    outgoing: [] as OutgoingMessage[],
    runs: [] as OfficeRun[], events: [] as VisibleOfficeRunEvent[], humanInputs: [] as OfficeHumanInputChange[],
    cursors: new Map<string, number>(), history: new Map<string, VisibleOfficeRunEvent[]>(),
    sequence: 0, draft: '', mentions: [] as string[], replyRequestKey: '', scrollTop: 0, followLatest: true,
  }
}
