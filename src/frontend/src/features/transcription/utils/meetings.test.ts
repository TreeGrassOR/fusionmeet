import { describe, expect, it } from 'vitest'
import type { RecordingApi } from '@/features/recording/api/fetchRecording'
import { RecordingMode, RecordingStatus } from '@/features/recording/types'
import { ApiAccessLevel } from '@/features/rooms/api/ApiRoom'
import { type TranscriptApi, TranscriptStatus } from '../types'
import {
  Availability,
  getRecordingAvailability,
  getTranscriptAvailability,
  getTranscriptFormats,
  groupIntoMeetings,
  hasPendingItems,
  isVideoRecording,
} from './meetings'

// Local times, so the dates read the same in any test timezone.
const at = (day: number, hours: number, minutes = 0) =>
  new Date(2026, 9, day, hours, minutes).toISOString()

// The API returns updated_at (last status change) but RecordingApi omits it.
type RecordingWithUpdate = RecordingApi & { updated_at?: string }

const ROOM_A = { id: 'room-a', name: 'Daily', slug: 'daily' }
const ROOM_B = { id: 'room-b', name: 'Retro', slug: 'retro' }

const recording = (
  overrides: Partial<RecordingWithUpdate> = {}
): RecordingWithUpdate => ({
  id: 'rec-1',
  room: { ...ROOM_A, access_level: ApiAccessLevel.RESTRICTED },
  created_at: at(19, 10),
  updated_at: at(19, 11),
  key: 'recordings/rec-1.mp4',
  mode: RecordingMode.ScreenRecording,
  status: RecordingStatus.Saved,
  is_expired: false,
  expired_at: at(26, 10),
  ...overrides,
})

const transcript = (overrides: Partial<TranscriptApi> = {}): TranscriptApi => ({
  id: 'tr-1',
  room: ROOM_A,
  status: TranscriptStatus.Ready,
  language: 'ru',
  started_at: at(19, 10, 1),
  ended_at: at(19, 11),
  created_at: at(19, 10, 1),
  participants: [
    { identity: 'u1', name: 'Ivan Petrov' },
    { identity: 'u2', name: 'Maria Sokolova' },
  ],
  segments_count: 12,
  is_ready: true,
  downloads: {
    md: '/api/v1.0/transcripts/tr-1/download/?format=md',
    json: '/api/v1.0/transcripts/tr-1/download/?format=json',
  },
  ...overrides,
})

describe('getRecordingAvailability', () => {
  it.each([
    [RecordingStatus.Saved, Availability.Available],
    [RecordingStatus.NotificationSucceed, Availability.Available],
    [RecordingStatus.FailedToStop, Availability.Available],
    [RecordingStatus.ExternalProcessFailed, Availability.Available],
    [RecordingStatus.ExternalProcessSuccessful, Availability.Available],
    [RecordingStatus.Initiated, Availability.InProgress],
    [RecordingStatus.Active, Availability.InProgress],
    [RecordingStatus.Stopped, Availability.Processing],
    [RecordingStatus.Aborted, Availability.Failed],
    [RecordingStatus.Failed, Availability.Failed],
    [RecordingStatus.FailedToStart, Availability.Failed],
  ])('maps %s to %s', (status, expected) => {
    expect(getRecordingAvailability({ status, is_expired: false })).toBe(
      expected
    )
  })

  it('marks a saved but expired recording as expired', () => {
    expect(
      getRecordingAvailability({
        status: RecordingStatus.Saved,
        is_expired: true,
      })
    ).toBe(Availability.Expired)
  })
})

describe('getTranscriptAvailability and getTranscriptFormats', () => {
  it('offers both formats of a ready transcript', () => {
    const ready = transcript()
    expect(getTranscriptAvailability(ready)).toBe(Availability.Available)
    expect(getTranscriptFormats(ready)).toEqual(['md', 'json'])
  })

  it('offers only the formats the API returns', () => {
    const mdOnly = transcript({
      downloads: { md: '/md', json: null },
    })
    expect(getTranscriptFormats(mdOnly)).toEqual(['md'])
  })

  it('treats a ready transcript without any file as failed', () => {
    const noFiles = transcript({ downloads: { md: null, json: null } })
    expect(getTranscriptAvailability(noFiles)).toBe(Availability.Failed)
    expect(getTranscriptFormats(noFiles)).toEqual([])
  })

  it.each([
    [TranscriptStatus.Active, Availability.InProgress],
    [TranscriptStatus.Stopping, Availability.Processing],
    [TranscriptStatus.Failed, Availability.Failed],
  ])('maps %s to %s, without downloads', (status, expected) => {
    const pending = transcript({ status, downloads: { md: null, json: null } })
    expect(getTranscriptAvailability(pending)).toBe(expected)
    expect(getTranscriptFormats(pending)).toEqual([])
  })
})

describe('hasPendingItems', () => {
  it('is false when everything is final', () => {
    expect(
      hasPendingItems(
        [recording(), recording({ status: RecordingStatus.Failed })],
        [transcript(), transcript({ status: TranscriptStatus.Failed })]
      )
    ).toBe(false)
  })

  it('is true while a recording is running or saving', () => {
    expect(
      hasPendingItems([recording({ status: RecordingStatus.Stopped })], [])
    ).toBe(true)
    expect(
      hasPendingItems([recording({ status: RecordingStatus.Active })], [])
    ).toBe(true)
  })

  it('is true while a transcript is running or being finalized', () => {
    expect(
      hasPendingItems([], [transcript({ status: TranscriptStatus.Stopping })])
    ).toBe(true)
  })
})

describe('groupIntoMeetings', () => {
  const summary = (meetings: ReturnType<typeof groupIntoMeetings>) =>
    meetings.map((meeting) => [
      meeting.room.id,
      meeting.recordings.map((r) => r.id),
      meeting.transcripts.map((t) => t.id),
    ])

  it('groups an overlapping recording and transcript of the same room', () => {
    const meetings = groupIntoMeetings([recording()], [transcript()])

    expect(meetings).toHaveLength(1)
    expect(meetings[0].room).toEqual(
      expect.objectContaining({ id: 'room-a', name: 'Daily' })
    )
    expect(meetings[0].recordings.map((r) => r.id)).toEqual(['rec-1'])
    expect(meetings[0].transcripts.map((t) => t.id)).toEqual(['tr-1'])
    expect(meetings[0].startedAt).toEqual(new Date(at(19, 10)))
    expect(meetings[0].participants).toEqual(['Ivan Petrov', 'Maria Sokolova'])
  })

  it('groups a transcript started and stopped during a recording', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec',
          created_at: at(19, 10),
          updated_at: at(19, 12),
        }),
      ],
      [
        transcript({
          id: 'tr',
          started_at: at(19, 10, 45),
          ended_at: at(19, 11, 15),
        }),
      ]
    )

    expect(summary(meetings)).toEqual([['room-a', ['rec'], ['tr']]])
  })

  it('splits two meetings of the same room on the same day', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec-morning',
          created_at: at(19, 9),
          updated_at: at(19, 10),
        }),
        recording({
          id: 'rec-afternoon',
          created_at: at(19, 14),
          updated_at: at(19, 15),
        }),
      ],
      [
        transcript({
          id: 'tr-morning',
          started_at: at(19, 9, 5),
          ended_at: at(19, 9, 55),
        }),
        transcript({
          id: 'tr-afternoon',
          started_at: at(19, 14, 2),
          ended_at: at(19, 14, 58),
        }),
      ]
    )

    expect(summary(meetings)).toEqual([
      ['room-a', ['rec-afternoon'], ['tr-afternoon']],
      ['room-a', ['rec-morning'], ['tr-morning']],
    ])
    expect(meetings.map((meeting) => meeting.startedAt)).toEqual([
      new Date(at(19, 14)),
      new Date(at(19, 9)),
    ])
    expect(new Set(meetings.map((meeting) => meeting.key)).size).toBe(2)
  })

  it('joins items less than 30 minutes apart, splits from 30 minutes', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'first',
          created_at: at(19, 10),
          updated_at: at(19, 11),
        }),
        // 29 minutes after the end of the first one.
        recording({
          id: 'close',
          created_at: at(19, 11, 29),
          updated_at: at(19, 12),
        }),
        // Exactly 30 minutes after the end of the previous one.
        recording({
          id: 'far',
          created_at: at(19, 12, 30),
          updated_at: at(19, 13),
        }),
      ],
      []
    )

    expect(summary(meetings)).toEqual([
      ['room-a', ['far'], []],
      ['room-a', ['first', 'close'], []],
    ])
  })

  it('chains items of a long meeting through their end times', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec-long',
          created_at: at(19, 9),
          updated_at: at(19, 12),
        }),
        recording({
          id: 'rec-after',
          created_at: at(19, 12, 20),
          updated_at: at(19, 12, 40),
        }),
      ],
      [
        // Starts hours after the first recording, but while it still runs.
        transcript({
          id: 'tr-inside',
          started_at: at(19, 11, 30),
          ended_at: at(19, 11, 50),
        }),
      ]
    )

    expect(summary(meetings)).toEqual([
      ['room-a', ['rec-long', 'rec-after'], ['tr-inside']],
    ])
  })

  it('keeps in-progress items without end open until now', () => {
    const now = new Date(at(19, 13))
    const meetings = groupIntoMeetings(
      [
        // Still recording, started long after the transcription.
        recording({
          id: 'rec-running',
          status: RecordingStatus.Active,
          created_at: at(19, 12, 30),
          updated_at: at(19, 12, 30),
        }),
      ],
      [
        transcript({
          id: 'tr-running',
          status: TranscriptStatus.Active,
          started_at: at(19, 10),
          ended_at: null,
          downloads: { md: null, json: null },
        }),
        // An earlier meeting of the same room, finished hours before.
        transcript({
          id: 'tr-earlier',
          started_at: at(19, 8),
          ended_at: at(19, 9),
        }),
      ],
      now
    )

    expect(summary(meetings)).toEqual([
      ['room-a', ['rec-running'], ['tr-running']],
      ['room-a', [], ['tr-earlier']],
    ])
  })

  it('keeps a running recording open until now', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec-running',
          status: RecordingStatus.Initiated,
          created_at: at(19, 10),
          updated_at: at(19, 10),
        }),
      ],
      [
        transcript({
          id: 'tr-later',
          status: TranscriptStatus.Active,
          started_at: at(19, 11, 30),
          ended_at: null,
          downloads: { md: null, json: null },
        }),
      ],
      new Date(at(19, 12))
    )

    expect(summary(meetings)).toEqual([
      ['room-a', ['rec-running'], ['tr-later']],
    ])
  })

  it('does not stretch finished items without end', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec-failed',
          status: RecordingStatus.Failed,
          created_at: at(19, 10),
          updated_at: undefined,
        }),
      ],
      [
        transcript({
          id: 'tr-failed',
          status: TranscriptStatus.Failed,
          started_at: at(19, 11),
          ended_at: null,
          downloads: { md: null, json: null },
        }),
      ],
      new Date(at(19, 18))
    )

    expect(summary(meetings)).toEqual([
      ['room-a', [], ['tr-failed']],
      ['room-a', ['rec-failed'], []],
    ])
  })

  it('does not let a transcript failed days later absorb the meetings since', () => {
    // A transcript stuck active is marked failed, and its end set, only when
    // the next transcription of its room starts.
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec-between',
          created_at: at(20, 15),
          updated_at: at(20, 16),
        }),
      ],
      [
        transcript({
          id: 'tr-stale',
          status: TranscriptStatus.Failed,
          started_at: at(18, 10),
          ended_at: at(21, 10),
          downloads: { md: null, json: null },
        }),
        transcript({
          id: 'tr-new',
          started_at: at(21, 10),
          ended_at: at(21, 11),
        }),
      ]
    )

    expect(summary(meetings)).toEqual([
      ['room-a', [], ['tr-new']],
      ['room-a', ['rec-between'], []],
      ['room-a', [], ['tr-stale']],
    ])
  })

  it('does not let an item stuck in progress absorb the next meetings', () => {
    const meetings = groupIntoMeetings(
      [
        recording({
          id: 'rec-stuck',
          status: RecordingStatus.Active,
          created_at: at(19, 10),
          updated_at: at(19, 10),
        }),
      ],
      [
        // Long after any real meeting ends.
        transcript({
          id: 'tr-next-day',
          started_at: at(20, 10),
          ended_at: at(20, 11),
        }),
      ],
      new Date(at(20, 12))
    )

    expect(summary(meetings)).toEqual([
      ['room-a', [], ['tr-next-day']],
      ['room-a', ['rec-stuck'], []],
    ])
  })

  it('splits by room and by day, the most recent meeting first', () => {
    const meetings = groupIntoMeetings(
      [
        recording({ id: 'rec-a-19', created_at: at(19, 9) }),
        recording({
          id: 'rec-b-19',
          room: { ...ROOM_B, access_level: ApiAccessLevel.PUBLIC },
          created_at: at(19, 9, 30),
        }),
      ],
      [
        transcript({
          id: 'tr-a-20',
          started_at: at(20, 8),
          ended_at: at(20, 9),
          created_at: at(20, 8),
        }),
      ]
    )

    expect(summary(meetings)).toEqual([
      ['room-a', [], ['tr-a-20']],
      ['room-b', ['rec-b-19'], []],
      ['room-a', ['rec-a-19'], []],
    ])
  })

  it('sorts the items of a meeting chronologically and keeps its earliest start', () => {
    const [meeting] = groupIntoMeetings(
      [
        recording({
          id: 'late',
          created_at: at(19, 10, 20),
          updated_at: at(19, 11),
        }),
        recording({
          id: 'early',
          created_at: at(19, 9),
          updated_at: at(19, 10),
        }),
      ],
      [
        transcript({
          id: 'tr-late',
          started_at: at(19, 10, 30),
          ended_at: at(19, 11),
        }),
        transcript({
          id: 'tr-early',
          started_at: at(19, 8, 30),
          ended_at: at(19, 10),
        }),
      ]
    )

    expect(meeting.recordings.map((r) => r.id)).toEqual(['early', 'late'])
    expect(meeting.transcripts.map((t) => t.id)).toEqual([
      'tr-early',
      'tr-late',
    ])
    expect(meeting.startedAt).toEqual(new Date(at(19, 8, 30)))
  })

  it('lists duplicated API rows once', () => {
    const [meeting] = groupIntoMeetings(
      [recording(), recording()],
      [transcript(), transcript()]
    )

    expect(meeting.recordings).toHaveLength(1)
    expect(meeting.transcripts).toHaveLength(1)
  })

  it('merges the speakers of several transcripts, in order of appearance', () => {
    const [meeting] = groupIntoMeetings(
      [],
      [
        transcript({
          id: 'second',
          started_at: at(19, 11, 10),
          ended_at: at(19, 12),
          participants: [
            { identity: 'u3', name: 'Oleg' },
            { identity: 'u1', name: 'Ivan Petrov' },
          ],
        }),
        transcript({
          id: 'first',
          started_at: at(19, 10),
          ended_at: at(19, 11),
          participants: [
            { identity: 'u1', name: 'Ivan Petrov' },
            { identity: 'u4', name: '' },
          ],
        }),
      ]
    )

    expect(meeting.participants).toEqual(['Ivan Petrov', 'u4', 'Oleg'])
  })

  it('falls back to the creation date of a transcript without start', () => {
    const [meeting] = groupIntoMeetings(
      [],
      [transcript({ started_at: '', created_at: at(21, 14) })]
    )

    expect(meeting.startedAt).toEqual(new Date(at(21, 14)))
  })

  it('returns no meeting without items', () => {
    expect(groupIntoMeetings([], [])).toEqual([])
  })
})

describe('isVideoRecording', () => {
  it('tells video recordings from audio-only ones', () => {
    expect(isVideoRecording({ mode: RecordingMode.ScreenRecording })).toBe(true)
    expect(isVideoRecording({ mode: RecordingMode.Transcript })).toBe(false)
  })
})
