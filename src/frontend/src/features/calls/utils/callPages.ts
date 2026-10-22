import type { InfiniteData } from '@tanstack/react-query'
import type { CallApi, PaginatedResponse } from '../api/types'

export type CallPages = InfiniteData<PaginatedResponse<CallApi>, number>

/**
 * The calls of the loaded pages, each once. The list is ordered by the last
 * activity and paginated by page number: when a call starts or gets a message
 * after a page was loaded, the next page starts with a call already shown.
 * The first one is kept (react-aria collections need unique keys, a repeated
 * one makes them loop forever).
 */
export const flattenCallPages = (
  pages: readonly PaginatedResponse<CallApi>[] | undefined
): CallApi[] => {
  const seen = new Set<string>()
  const calls: CallApi[] = []
  for (const page of pages ?? []) {
    for (const call of page.results) {
      if (seen.has(call.id)) continue
      seen.add(call.id)
      calls.push(call)
    }
  }
  return calls
}

/**
 * The loaded pages with a call marked read (no unread badge), the same
 * object when there is nothing to change.
 */
export const markCallReadInPages = (
  data: CallPages | undefined,
  callId: string
): CallPages | undefined => {
  if (!data) return data
  const isUnread = (call: CallApi) => call.id === callId && call.unread_count
  if (!data.pages.some((page) => page.results.some(isUnread))) return data
  return {
    ...data,
    pages: data.pages.map((page) =>
      page.results.some(isUnread)
        ? {
            ...page,
            results: page.results.map((call) =>
              isUnread(call) ? { ...call, unread_count: 0 } : call
            ),
          }
        : page
    ),
  }
}
