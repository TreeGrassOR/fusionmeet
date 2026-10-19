import type { RecordingApi } from '@/features/recording/api/fetchRecording'
import { RecordingMode, RecordingStatus } from '@/features/recording/types'
import {
  type TranscriptApi,
  type TranscriptFormat,
  TranscriptStatus,
} from '../types'

/**
 * What a user can do with a recording or a transcript listed on the
 * "My recordings" page.
 */
export enum Availability {
  Available = 'available',
  InProgress = 'inProgress',
  Processing = 'processing',
  Expired = 'expired',
  Failed = 'failed',
}

// Same set as the recording download page: the file exists in the storage.
const SAVED_RECORDING_STATUSES: RecordingStatus[] = [
  RecordingStatus.Saved,
  RecordingStatus.NotificationSucceed,
  RecordingStatus.FailedToStop,
  RecordingStatus.ExternalProcessFailed,
  RecordingStatus.ExternalProcessSuccessful,
]

const RUNNING_RECORDING_STATUSES: RecordingStatus[] = [
  RecordingStatus.Initiated,
  RecordingStatus.Active,
]

export const getRecordingAvailability = (
  recording: Pick<RecordingApi, 'status' | 'is_expired'>
): Availability => {
  if (SAVED_RECORDING_STATUSES.includes(recording.status)) {
    return recording.is_expired ? Availability.Expired : Availability.Available
  }
  if (RUNNING_RECORDING_STATUSES.includes(recording.status)) {
    return Availability.InProgress
  }
  if (recording.status === RecordingStatus.Stopped) {
    return Availability.Processing
  }
  return Availability.Failed
}

export const getTranscriptAvailability = (
  transcript: Pick<TranscriptApi, 'status' | 'downloads'>
): Availability => {
  switch (transcript.status) {
    case TranscriptStatus.Ready:
      return transcript.downloads?.md || transcript.downloads?.json
        ? Availability.Available
        : Availability.Failed
    case TranscriptStatus.Active:
      return Availability.InProgress
    case TranscriptStatus.Stopping:
      return Availability.Processing
    default:
      return Availability.Failed
  }
}

/**
 * Formats a ready transcript can be downloaded in, in display order.
 */
export const getTranscriptFormats = (
  transcript: Pick<TranscriptApi, 'status' | 'downloads'>
): TranscriptFormat[] => {
  if (getTranscriptAvailability(transcript) !== Availability.Available) {
    return []
  }
  return (['md', 'json'] as const).filter(
    (format) => !!transcript.downloads?.[format]
  )
}

/**
 * True while something listed may still change status, so the page keeps
 * polling the API.
 */
export const hasPendingItems = (
  recordings: Pick<RecordingApi, 'status' | 'is_expired'>[],
  transcripts: Pick<TranscriptApi, 'status' | 'downloads'>[]
) => {
  const isPending = (availability: Availability) =>
    availability === Availability.InProgress ||
    availability === Availability.Processing
  return (
    recordings.some((r) => isPending(getRecordingAvailability(r))) ||
    transcripts.some((t) => isPending(getTranscriptAvailability(t)))
  )
}

export type MeetingRoom = Pick<TranscriptApi['room'], 'id' | 'name' | 'slug'>

export type Meeting = {
  key: string
  room: MeetingRoom
  // Earliest start of the meeting's recordings and transcripts.
  startedAt: Date
  recordings: RecordingApi[]
  transcripts: TranscriptApi[]
  // Display names of the participants who spoke, in order of appearance.
  participants: string[]
}

export const getRecordingStart = (
  recording: Pick<RecordingApi, 'created_at'>
) => new Date(recording.created_at)

export const getTranscriptStart = (
  transcript: Pick<TranscriptApi, 'started_at' | 'created_at'>
) => new Date(transcript.started_at || transcript.created_at)

const uniqueById = <T extends { id: string }>(items: T[]) => {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.id)) return false
    seen.add(item.id)
    return true
  })
}

// Recordings and transcripts of a room whose time spans overlap or are
// closer than this belong to the same meeting.
export const MEETING_MAX_GAP_MS = 30 * 60 * 1000

// Longest an item keeps its meeting open. It bounds items stuck in progress
// (a lost webhook, a crashed agent) and stale transcripts, which the backend
// marks failed, and gives an end, only when their room's next transcription
// starts, possibly days later.
export const MEETING_MAX_ITEM_MS = 12 * 60 * 60 * 1000

const isValidDate = (date: Date) => !Number.isNaN(date.getTime())

const latest = (a: Date, b: Date) => (b > a ? b : a)

const boundedEnd = (start: Date, end: Date) =>
  end.getTime() - start.getTime() > MEETING_MAX_ITEM_MS
    ? new Date(start.getTime() + MEETING_MAX_ITEM_MS)
    : end

/**
 * When a recording ended. A running one lasts until `now`. Otherwise its
 * last status change (`updated_at`, returned by the API but not part of
 * RecordingApi) is when it stopped; without it, only its start is known.
 */
const getRecordingEnd = (
  recording: Pick<RecordingApi, 'status' | 'created_at'> & {
    updated_at?: string
  },
  now: Date
) => {
  const start = getRecordingStart(recording)
  if (RUNNING_RECORDING_STATUSES.includes(recording.status)) {
    return latest(start, now)
  }
  const updatedAt = new Date(recording.updated_at ?? '')
  return isValidDate(updatedAt) ? latest(start, updatedAt) : start
}

/**
 * When a transcript ended. One still running, without `ended_at`, lasts
 * until `now`.
 */
const getTranscriptEnd = (
  transcript: Pick<
    TranscriptApi,
    'status' | 'started_at' | 'created_at' | 'ended_at'
  >,
  now: Date
) => {
  const start = getTranscriptStart(transcript)
  const endedAt = new Date(transcript.ended_at ?? '')
  if (isValidDate(endedAt)) return latest(start, endedAt)
  if (
    transcript.status === TranscriptStatus.Active ||
    transcript.status === TranscriptStatus.Stopping
  ) {
    return latest(start, now)
  }
  return start
}

type MeetingItem = {
  room: MeetingRoom
  start: Date
  end: Date
  addTo: (meeting: Meeting) => void
}

/**
 * Group recordings and transcripts into meetings: items of the same room
 * whose time spans overlap or are less than MEETING_MAX_GAP_MS apart. Items
 * still in progress last until `now`, and no item spans more than
 * MEETING_MAX_ITEM_MS. Meetings are sorted from the most
 * recent one, their items chronologically. Duplicated API rows (a user can
 * reach a recording through several accesses) are listed once.
 */
export const groupIntoMeetings = (
  recordings: RecordingApi[],
  transcripts: TranscriptApi[],
  now: Date = new Date()
): Meeting[] => {
  const items: MeetingItem[] = [
    ...uniqueById(recordings).map((recording) => ({
      room: recording.room,
      start: getRecordingStart(recording),
      end: getRecordingEnd(recording, now),
      addTo: (meeting: Meeting) => meeting.recordings.push(recording),
    })),
    ...uniqueById(transcripts).map((transcript) => ({
      room: transcript.room,
      start: getTranscriptStart(transcript),
      end: getTranscriptEnd(transcript, now),
      addTo: (meeting: Meeting) => meeting.transcripts.push(transcript),
    })),
  ].sort((a, b) => a.start.getTime() - b.start.getTime())

  const meetings: Meeting[] = []
  // Latest meeting of each room and when its last item ends.
  const lastByRoom = new Map<string, { meeting: Meeting; end: Date }>()

  items.forEach((item) => {
    const end = boundedEnd(item.start, item.end)
    const last = lastByRoom.get(item.room.id)
    if (
      last &&
      item.start.getTime() - last.end.getTime() < MEETING_MAX_GAP_MS
    ) {
      last.end = latest(last.end, end)
      item.addTo(last.meeting)
      return
    }
    const meeting: Meeting = {
      key: `${item.room.id}|${item.start.getTime()}`,
      room: item.room,
      startedAt: item.start,
      recordings: [],
      transcripts: [],
      participants: [],
    }
    item.addTo(meeting)
    meetings.push(meeting)
    lastByRoom.set(item.room.id, { meeting, end })
  })

  return meetings
    .map((meeting) => {
      const names = meeting.transcripts.flatMap((transcript) =>
        (transcript.participants ?? []).map(
          (participant) => participant.name || participant.identity
        )
      )
      meeting.participants = Array.from(new Set(names.filter(Boolean)))
      return meeting
    })
    .sort((a, b) => b.startedAt.getTime() - a.startedAt.getTime())
}

export const isVideoRecording = (recording: Pick<RecordingApi, 'mode'>) =>
  recording.mode !== RecordingMode.Transcript
