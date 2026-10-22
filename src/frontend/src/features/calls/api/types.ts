import type { RecordingMode, RecordingStatus } from '@/features/recording/types'
import type { TranscriptStatus } from '@/features/transcription/types'

export type { PaginatedResponse } from '@/features/transcription/types'

/**
 * A participant of a call, as listed with the call (GET /calls/).
 */
export type CallParticipantApi = {
  name: string
  is_me: boolean
}

export type CallLastMessageApi = {
  sender_name: string
  text: string
  sent_at: string
  is_mine: boolean
}

/**
 * A call: one session of a room, from its start to its end (GET /calls/).
 * `title` is empty when the call has none: the frontend builds one from the
 * participants or the date (see utils/callTitle.ts).
 */
export type CallApi = {
  id: string
  room: { id: string; slug: string }
  title: string
  started_at: string
  ended_at: string | null
  is_active: boolean
  // the first participants only, `participants_count` counts all of them
  participants: CallParticipantApi[]
  participants_count: number
  // the user is one of the participants, listed or not
  participated: boolean
  last_message: CallLastMessageApi | null
  messages_count: number
  unread_count: number
  has_transcript: boolean
  has_recording: boolean
}

export type CallDetailParticipantApi = CallParticipantApi & {
  is_guest: boolean
  joined_at: string
  left_at: string | null
}

export type CallTranscriptApi = {
  id: string
  status: TranscriptStatus
  started_at: string
  ended_at: string | null
  // whether GET /calls/<id>/transcripts/<tid>/ is allowed
  can_view: boolean
}

export type CallRecordingApi = {
  id: string
  status: RecordingStatus
  mode: RecordingMode
  created_at: string
}

/**
 * A call with all its participants, transcripts and recordings
 * (GET /calls/<id>/).
 */
export type CallDetailApi = Omit<CallApi, 'participants'> & {
  participants: CallDetailParticipantApi[]
  transcripts: CallTranscriptApi[]
  recordings: CallRecordingApi[]
  // the call goes on and the user may open its room
  can_rejoin: boolean
}

/**
 * A chat message of a call (GET /calls/<id>/messages/), oldest first.
 */
export type CallMessageApi = {
  id: string
  // the same for the messages of one sender in the call (two senders may
  // share a name)
  sender_key: string
  sender_name: string
  sender_is_me: boolean
  text: string
  sent_at: string
}

/**
 * The JSON document of a ready transcript, written by the transcription
 * agent (docs/features/gigaam-transcription.md).
 */
export type TranscriptDocument = {
  version: number
  transcript_id: string
  language: string
  room: { id: string; name: string }
  started_at: string
  ended_at: string
  participants: { identity: string; name: string }[]
  models?: { id: string; label: string }[]
  segments: {
    identity: string
    name: string
    start: string
    end: string
    text: string
    model?: string
  }[]
}
