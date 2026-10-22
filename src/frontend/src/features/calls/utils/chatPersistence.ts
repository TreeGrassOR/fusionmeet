import { ApiError } from '@/api/ApiError'
import type { ChatRow } from '@/stores/chat'
import type { ChatMessagePayload } from '../api/postChatMessage'

// Longest message the backend stores, as the chat input allows
export const MAX_MESSAGE_LENGTH = 2000

/**
 * The chat rows to store: the messages the local participant sent that were
 * not handled yet. Rows received from others are stored by their sender.
 */
export const selectRowsToPersist = (
  rows: readonly ChatRow[],
  handledIds: ReadonlySet<string>
): ChatRow[] =>
  rows.filter(
    (row) =>
      row.isLocal &&
      !handledIds.has(row.id) &&
      !!row.message.trim() &&
      row.message.length <= MAX_MESSAGE_LENGTH
  )

export const toChatMessagePayload = (
  row: ChatRow,
  { roomSid, name }: { roomSid: string | null; name: string | null }
): ChatMessagePayload => ({
  id: row.id,
  text: row.message,
  sent_at: Math.round(row.timestamp),
  room_sid: roomSid || null,
  name: name || null,
})

/**
 * Network errors (fetch rejects with a TypeError), server errors (among them
 * 503 when the backend could not ask LiveKit) and throttling are worth
 * retrying. Any other 4xx (invalid message, sender not connected to the call,
 * no live call matching the message) and any other error are final.
 */
export const isRetryableError = (error: unknown) =>
  error instanceof ApiError
    ? error.statusCode >= 500 || error.statusCode === 429
    : error instanceof TypeError

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds))

/**
 * Run a request, then retry it up to `retries` times, waiting
 * `baseDelayMs`, then twice as long each time.
 */
export const withRetries = async <T>(
  request: () => Promise<T>,
  {
    retries = 3,
    baseDelayMs = 1000,
    sleep = wait,
  }: {
    retries?: number
    baseDelayMs?: number
    sleep?: (milliseconds: number) => Promise<void>
  } = {}
): Promise<T> => {
  for (let attempt = 0; ; attempt++) {
    try {
      return await request()
    } catch (error) {
      if (attempt >= retries || !isRetryableError(error)) throw error
      await sleep(baseDelayMs * 2 ** attempt)
    }
  }
}
