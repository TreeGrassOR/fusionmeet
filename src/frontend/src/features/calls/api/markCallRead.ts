import { useMutation } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import { queryClient } from '@/api/queryClient'
import { callsKeys } from './queryKeys'

export const markCallRead = (callId: string) =>
  fetchApi<void>(`calls/${callId}/read/`, { method: 'POST' })

/**
 * Mark the messages of a call as read, then refresh the unread badges.
 */
export const useMarkCallRead = () =>
  useMutation({
    mutationFn: markCallRead,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: [callsKeys.list] }),
  })
