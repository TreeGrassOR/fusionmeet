import { useMutation } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import { queryClient } from '@/api/queryClient'
import { markCallReadInPages, type CallPages } from '../utils/callPages'
import { callsKeys } from './queryKeys'

export const markCallRead = (callId: string) =>
  fetchApi<void>(`calls/${callId}/read/`, { method: 'POST' })

/**
 * Mark the messages of a call as read, then clear its unread badge in the
 * loaded lists (every search), without fetching them again.
 */
export const useMarkCallRead = () =>
  useMutation({
    mutationFn: markCallRead,
    onSuccess: (_data, callId) =>
      queryClient.setQueriesData<CallPages>(
        { queryKey: [callsKeys.list] },
        (data) => markCallReadInPages(data, callId)
      ),
  })
