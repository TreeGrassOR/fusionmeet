import { RecordingStatus } from '@/features/recording/types'

// The recordings the download page (/recording/<id>) offers to download:
// keep in sync with `isSaved` in features/recording/routes/RecordingDownload
const DOWNLOADABLE_STATUSES: readonly RecordingStatus[] = [
  RecordingStatus.Saved,
  RecordingStatus.NotificationSucceed,
  RecordingStatus.ExternalProcessSuccessful,
  RecordingStatus.FailedToStop,
  RecordingStatus.ExternalProcessFailed,
]

const RUNNING_STATUSES: readonly RecordingStatus[] = [
  RecordingStatus.Initiated,
  RecordingStatus.Active,
]

export type RecordingState = 'ready' | 'inProgress' | 'processing' | 'failed'

/**
 * What a recording of a call is at: `ready` ones are the only ones with
 * something to open.
 */
export const getRecordingState = (status: RecordingStatus): RecordingState => {
  if (DOWNLOADABLE_STATUSES.includes(status)) return 'ready'
  if (RUNNING_STATUSES.includes(status)) return 'inProgress'
  if (status === RecordingStatus.Stopped) return 'processing'
  return 'failed'
}
