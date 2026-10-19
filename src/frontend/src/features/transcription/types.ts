import type { ApiRoom } from '@/features/rooms/api/ApiRoom'

/**
 * Statuses of a transcription, as written by the backend in the room
 * metadata key "transcription". The key is absent when nothing runs.
 */
export enum TranscriptionMetadataStatus {
  Started = 'started',
  Stopping = 'stopping',
}

export type TranscriptionMetadata = {
  id: string
  status: TranscriptionMetadataStatus
  room_name?: string
  started_at?: string
  started_by?: string
}

/**
 * Backend status of a transcript (GET /transcripts/).
 */
export enum TranscriptStatus {
  Active = 'active',
  Stopping = 'stopping',
  Ready = 'ready',
  Failed = 'failed',
}

export type TranscriptFormat = 'md' | 'json'

export type TranscriptParticipant = {
  identity: string
  name: string
}

export type TranscriptApi = {
  id: string
  room: Pick<ApiRoom, 'id' | 'name' | 'slug'>
  status: TranscriptStatus
  language: string
  started_at: string
  ended_at: string | null
  created_at: string
  participants: TranscriptParticipant[]
  segments_count: number
  // Ids of the recognition models that produced the phrases, in order of
  // first use. Absent or empty for transcripts made before the model choice.
  models?: string[]
  is_ready: boolean
  downloads: Record<TranscriptFormat, string | null>
}

export type PaginatedResponse<T> = {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

/**
 * Load status of a recognition model in the ASR service.
 */
export enum AsrModelStatus {
  Ready = 'ready',
  Loading = 'loading',
  Failed = 'failed',
}

/**
 * Recognition model offered by the ASR service (GET /transcription/models/).
 * The label and the description are written by the ASR service, in Russian.
 */
export type AsrModelApi = {
  id: string
  label: string
  description: string
  languages: string[]
  punctuation: boolean
  device: 'cuda' | 'cpu' | null
  status: AsrModelStatus
  error: string | null
}

export type AsrModelsApi = {
  // Id of the model used by the rooms that did not choose one.
  default: string
  models: AsrModelApi[]
}

/**
 * Recognition model chosen in a room, null for the default model of the ASR
 * service (GET and PUT /rooms/<id>/transcription-settings/).
 */
export type RoomTranscriptionSettingsApi = {
  asr_model: string | null
}
