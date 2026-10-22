import { useQuery } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import { TranscriptStatus } from '@/features/transcription/types'
import { callsKeys } from './queryKeys'
import type { CallDetailApi } from './types'

// While the call goes on or a transcript is being written
const DETAIL_POLLING_INTERVAL_MS = 10_000

export const fetchCall = (callId: string, signal?: AbortSignal) =>
  fetchApi<CallDetailApi>(`calls/${callId}/`, { signal })

export const isCallDetailPending = (call: CallDetailApi | undefined) =>
  !!call &&
  (call.is_active ||
    call.transcripts.some(
      (transcript) =>
        transcript.status === TranscriptStatus.Active ||
        transcript.status === TranscriptStatus.Stopping
    ))

export const useCall = (callId: string) =>
  useQuery({
    queryKey: [callsKeys.detail, callId],
    queryFn: ({ signal }) => fetchCall(callId, signal),
    retry: false,
    refetchInterval: (query) =>
      isCallDetailPending(query.state.data)
        ? DETAIL_POLLING_INTERVAL_MS
        : false,
  })
