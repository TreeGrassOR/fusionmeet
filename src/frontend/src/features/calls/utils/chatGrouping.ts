import type { CallMessageApi } from '../api/types'

// Same rule as the in-call chat (stores/chat.ts)
export const GROUPING_WINDOW_MS = 60_000

export type ChatHistoryRow = CallMessageApi & {
  // epoch milliseconds of `sent_at`
  timestamp: number
  // first message of a group: show the sender and the time
  showHeader: boolean
  // first message of another day than the previous one
  isNewDay: boolean
}

// by sender, not by name: two guests may both be «Гость»
const sameSender = (a: CallMessageApi, b: CallMessageApi) =>
  a.sender_key && b.sender_key
    ? a.sender_key === b.sender_key
    : a.sender_is_me === b.sender_is_me && a.sender_name === b.sender_name

const dayOf = (timestamp: number) => new Date(timestamp).toDateString()

/**
 * Group the messages of a call (oldest first) like the in-call chat: the
 * messages one sender writes less than a minute after their previous one
 * share its header.
 */
export const groupMessages = (
  messages: readonly CallMessageApi[]
): ChatHistoryRow[] => {
  const rows: ChatHistoryRow[] = []
  for (const message of messages) {
    const timestamp = new Date(message.sent_at).getTime()
    const prev = rows[rows.length - 1]
    const isNewDay = !!prev && dayOf(prev.timestamp) !== dayOf(timestamp)
    rows.push({
      ...message,
      timestamp,
      isNewDay,
      showHeader:
        !prev ||
        isNewDay ||
        !sameSender(prev, message) ||
        timestamp - prev.timestamp >= GROUPING_WINDOW_MS,
    })
  }
  return rows
}
