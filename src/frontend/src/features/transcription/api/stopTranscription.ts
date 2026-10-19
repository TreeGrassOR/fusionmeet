import { useMutation, type UseMutationOptions } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import type { ApiError } from '@/api/ApiError'
import type { TranscriptApi } from '../types'
import { transcriptionKeys } from './queryKeys'

export interface StopTranscriptionParams {
  // Room UUID (the LiveKit room name).
  id: string
}

export const stopTranscription = ({
  id,
}: StopTranscriptionParams): Promise<TranscriptApi> => {
  return fetchApi<TranscriptApi>(`rooms/${id}/stop-transcription/`, {
    method: 'POST',
  })
}

export function useStopTranscription(
  options?: UseMutationOptions<TranscriptApi, ApiError, StopTranscriptionParams>
) {
  return useMutation<TranscriptApi, ApiError, StopTranscriptionParams>({
    mutationKey: [transcriptionKeys.stopTranscription],
    mutationFn: stopTranscription,
    onSuccess: options?.onSuccess,
    onError: options?.onError,
  })
}
