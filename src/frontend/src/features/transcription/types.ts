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
  is_ready: boolean
  downloads: Record<TranscriptFormat, string | null>
}

export type PaginatedResponse<T> = {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}
