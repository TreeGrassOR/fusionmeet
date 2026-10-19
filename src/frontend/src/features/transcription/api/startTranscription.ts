import { useMutation, type UseMutationOptions } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import type { ApiError } from '@/api/ApiError'
import type { TranscriptApi } from '../types'
import { transcriptionKeys } from './queryKeys'

export interface StartTranscriptionParams {
  // Room UUID (the LiveKit room name).
  id: string
}

export const startTranscription = ({
  id,
}: StartTranscriptionParams): Promise<TranscriptApi> => {
  return fetchApi<TranscriptApi>(`rooms/${id}/start-transcription/`, {
    method: 'POST',
  })
}

export function useStartTranscription(
  options?: UseMutationOptions<
    TranscriptApi,
    ApiError,
    StartTranscriptionParams
  >
) {
  return useMutation<TranscriptApi, ApiError, StartTranscriptionParams>({
    mutationKey: [transcriptionKeys.startTranscription],
    mutationFn: startTranscription,
    onSuccess: options?.onSuccess,
    onError: options?.onError,
  })
}
