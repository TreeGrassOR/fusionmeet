import { fetchApi } from '@/api/fetchApi'
import type { RecordingApi } from '@/features/recording/api/fetchRecording'
import type { PaginatedResponse } from '../types'
import { fetchAllPages, MAX_PAGE_SIZE } from './fetchAllPages'

export const fetchRecordingsPage = (page: number) =>
  fetchApi<PaginatedResponse<RecordingApi>>(
    `recordings/?page=${page}&page_size=${MAX_PAGE_SIZE}`
  )

/**
 * Every recording the current user has access to, most recent first.
 */
export const listRecordings = () => fetchAllPages(fetchRecordingsPage)
