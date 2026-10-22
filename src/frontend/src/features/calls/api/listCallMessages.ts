import { useQuery } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import {
  fetchAllPages,
  MAX_PAGE_SIZE,
} from '@/features/transcription/api/fetchAllPages'
import { callsKeys } from './queryKeys'
import type { CallMessageApi, PaginatedResponse } from './types'

// New messages of a call that goes on
const MESSAGES_POLLING_INTERVAL_MS = 10_000

const fetchCallMessagesPage = (callId: string, page: number) =>
  fetchApi<PaginatedResponse<CallMessageApi>>(
    `calls/${callId}/messages/?page=${page}&page_size=${MAX_PAGE_SIZE}`
  )

/**
 * Every chat message of a call, oldest first.
 */
export const listCallMessages = (callId: string) =>
  fetchAllPages((page) => fetchCallMessagesPage(callId, page))

export const useCallMessages = (callId: string, isActive: boolean) =>
  useQuery({
    queryKey: [callsKeys.messages, callId],
    queryFn: () => listCallMessages(callId),
    retry: false,
    refetchInterval: isActive ? MESSAGES_POLLING_INTERVAL_MS : false,
  })
