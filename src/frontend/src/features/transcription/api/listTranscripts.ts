import { fetchApi } from '@/api/fetchApi'
import type { PaginatedResponse, TranscriptApi } from '../types'
import { fetchAllPages, MAX_PAGE_SIZE } from './fetchAllPages'

export const fetchTranscriptsPage = (page: number) =>
  fetchApi<PaginatedResponse<TranscriptApi>>(
    `transcripts/?page=${page}&page_size=${MAX_PAGE_SIZE}`
  )

/**
 * Every transcript the current user may see, most recent first.
 */
export const listTranscripts = () => fetchAllPages(fetchTranscriptsPage)
