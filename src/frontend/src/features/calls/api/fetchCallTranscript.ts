import { useQuery } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import { callsKeys } from './queryKeys'
import type { TranscriptDocument } from './types'

export const fetchCallTranscript = (callId: string, transcriptId: string) =>
  fetchApi<TranscriptDocument>(`calls/${callId}/transcripts/${transcriptId}/`)

/**
 * The document of a ready transcript. It no longer changes once ready.
 */
export const useCallTranscript = (
  callId: string,
  transcriptId: string | undefined
) =>
  useQuery({
    queryKey: [callsKeys.transcript, callId, transcriptId],
    queryFn: () => fetchCallTranscript(callId, transcriptId as string),
    enabled: !!transcriptId,
    staleTime: Infinity,
    retry: false,
  })
