import {
  type TranscriptionMetadata,
  TranscriptionMetadataStatus,
} from '../types'

// Room metadata key written by the backend while a transcription runs.
export const TRANSCRIPTION_METADATA_KEY = 'transcription'

export interface TranscriptionStatus {
  transcription?: TranscriptionMetadata
  // A transcription is running, or is being finalized.
  isActive: boolean
  isStarted: boolean
  isStopping: boolean
}

const STATUSES = Object.values(TranscriptionMetadataStatus) as string[]

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const optionalString = (value: unknown) =>
  typeof value === 'string' ? value : undefined

/**
 * Extract the transcription entry from the (already JSON-parsed) room
 * metadata. Returns undefined when it is absent or malformed, so a bad
 * value never shows a transcription that does not exist.
 */
export const parseTranscriptionMetadata = (
  metadata: unknown
): TranscriptionMetadata | undefined => {
  if (!isRecord(metadata)) return undefined

  const value = metadata[TRANSCRIPTION_METADATA_KEY]
  if (!isRecord(value)) return undefined

  const { id, status } = value
  if (typeof id !== 'string' || !id) return undefined
  if (typeof status !== 'string' || !STATUSES.includes(status)) return undefined

  return {
    id,
    status: status as TranscriptionMetadataStatus,
    room_name: optionalString(value.room_name),
    started_at: optionalString(value.started_at),
    started_by: optionalString(value.started_by),
  }
}

export const getTranscriptionStatus = (
  metadata: unknown
): TranscriptionStatus => {
  const transcription = parseTranscriptionMetadata(metadata)
  const isStarted =
    transcription?.status === TranscriptionMetadataStatus.Started
  const isStopping =
    transcription?.status === TranscriptionMetadataStatus.Stopping

  return {
    transcription,
    isActive: isStarted || isStopping,
    isStarted,
    isStopping,
  }
}
