import { useInfiniteQuery } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import { callsKeys } from './queryKeys'
import type { CallApi, PaginatedResponse } from './types'

export const CALLS_PAGE_SIZE = 30

// The list follows new calls and messages while the page is visible
// (react-query pauses the interval in a hidden tab).
const LIST_POLLING_INTERVAL_MS = 30_000

export const fetchCallsPage = ({ q, page }: { q: string; page: number }) => {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(CALLS_PAGE_SIZE),
  })
  if (q) params.set('q', q)
  return fetchApi<PaginatedResponse<CallApi>>(`calls/?${params}`)
}

/**
 * The calls of the logged-in user, most recent activity first, page by page.
 */
export const useCalls = (q: string) =>
  useInfiniteQuery({
    queryKey: [callsKeys.list, q],
    queryFn: ({ pageParam }) => fetchCallsPage({ q, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: (lastPage, _pages, lastPageParam) =>
      lastPage.next ? lastPageParam + 1 : undefined,
    refetchInterval: LIST_POLLING_INTERVAL_MS,
    retry: false,
  })
