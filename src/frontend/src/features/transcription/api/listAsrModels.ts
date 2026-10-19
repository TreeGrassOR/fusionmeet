import { useQuery } from '@tanstack/react-query'
import { fetchApi } from '@/api/fetchApi'
import type { AsrModelsApi } from '../types'
import { hasLoadingModels } from '../utils/asrModels'
import { transcriptionKeys } from './queryKeys'

// A model still loading becomes selectable within minutes: read the list
// again meanwhile. The backend caches such a list for a few seconds only.
const LOADING_POLLING_INTERVAL_MS = 10_000

/**
 * Recognition models of the ASR service, with their load status. The backend
 * answers 503 when the ASR service is not configured or cannot be reached.
 */
export const listAsrModels = () =>
  fetchApi<AsrModelsApi>('transcription/models/')

export const useAsrModels = ({
  enabled = true,
  pollWhileLoading = false,
}: {
  enabled?: boolean
  pollWhileLoading?: boolean
} = {}) =>
  useQuery({
    queryKey: [transcriptionKeys.asrModels],
    queryFn: listAsrModels,
    enabled,
    retry: false,
    refetchInterval: (query) =>
      pollWhileLoading && hasLoadingModels(query.state.data)
        ? LOADING_POLLING_INTERVAL_MS
        : false,
  })
