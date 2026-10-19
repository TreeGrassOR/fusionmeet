import {
  useMutation,
  useQuery,
  type UseMutationOptions,
} from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import type { ApiError } from '@/api/ApiError'
import { queryClient } from '@/api/queryClient'
import type { RoomTranscriptionSettingsApi } from '../types'
import { transcriptionKeys } from './queryKeys'

export interface UpdateRoomTranscriptionSettingsParams {
  // Room UUID (the LiveKit room name).
  id: string
  // Null resets the room to the default model of the ASR service.
  asrModel: string | null
}

type UpdateContext = {
  previous?: RoomTranscriptionSettingsApi
}

const settingsQueryKey = (id: string) => [
  transcriptionKeys.roomTranscriptionSettings,
  id,
]

export const fetchRoomTranscriptionSettings = (
  id: string
): Promise<RoomTranscriptionSettingsApi> =>
  fetchApi<RoomTranscriptionSettingsApi>(`rooms/${id}/transcription-settings/`)

export const updateRoomTranscriptionSettings = ({
  id,
  asrModel,
}: UpdateRoomTranscriptionSettingsParams): Promise<RoomTranscriptionSettingsApi> =>
  fetchApi<RoomTranscriptionSettingsApi>(
    `rooms/${id}/transcription-settings/`,
    {
      method: 'PUT',
      body: JSON.stringify({ asr_model: asrModel }),
    }
  )

/**
 * Recognition model chosen in a room. Requires the same permission as
 * starting the transcription.
 */
export const useRoomTranscriptionSettings = (id?: string) =>
  useQuery({
    queryKey: settingsQueryKey(id ?? ''),
    queryFn: () => fetchRoomTranscriptionSettings(id ?? ''),
    enabled: !!id,
    retry: false,
  })

/**
 * Save the recognition model of a room. The cache is updated right away and
 * restored if the backend refuses the model (unknown, or failed to load).
 */
export function useUpdateRoomTranscriptionSettings(
  options?: UseMutationOptions<
    RoomTranscriptionSettingsApi,
    ApiError,
    UpdateRoomTranscriptionSettingsParams,
    UpdateContext
  >
) {
  return useMutation<
    RoomTranscriptionSettingsApi,
    ApiError,
    UpdateRoomTranscriptionSettingsParams,
    UpdateContext
  >({
    mutationKey: [transcriptionKeys.updateRoomTranscriptionSettings],
    mutationFn: updateRoomTranscriptionSettings,
    onMutate: async ({ id, asrModel }) => {
      await queryClient.cancelQueries({ queryKey: settingsQueryKey(id) })
      const previous = queryClient.getQueryData<RoomTranscriptionSettingsApi>(
        settingsQueryKey(id)
      )
      queryClient.setQueryData<RoomTranscriptionSettingsApi>(
        settingsQueryKey(id),
        { asr_model: asrModel }
      )
      return { previous }
    },
    onError: (_error, { id }, context) => {
      if (context?.previous) {
        queryClient.setQueryData(settingsQueryKey(id), context.previous)
      }
      // The model may have failed to load since the list was read.
      queryClient.invalidateQueries({
        queryKey: [transcriptionKeys.asrModels],
      })
    },
    onSuccess: (data, { id }) => {
      queryClient.setQueryData(settingsQueryKey(id), data)
    },
    ...options,
  })
}
