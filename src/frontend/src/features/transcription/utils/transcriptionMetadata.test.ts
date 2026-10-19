import { describe, expect, it } from 'vitest'
import { TranscriptionMetadataStatus } from '../types'
import {
  getTranscriptionStatus,
  parseTranscriptionMetadata,
} from './transcriptionMetadata'

const transcription = {
  id: '6f1c2a1e-0d6b-4c2e-9d55-7c0a9f1b2c3d',
  status: 'started',
  room_name: 'Daily',
  started_at: '2026-10-19T10:00:00.000Z',
  started_by: 'Ivan Petrov',
}

describe('parseTranscriptionMetadata', () => {
  it('reads the transcription entry among the other room metadata keys', () => {
    expect(
      parseTranscriptionMetadata({
        recording_mode: 'screen_recording',
        recording_status: 'started',
        access_level: 'trusted',
        transcription,
      })
    ).toEqual({
      ...transcription,
      status: TranscriptionMetadataStatus.Started,
    })
  })

  it('keeps only the known fields, as strings', () => {
    expect(
      parseTranscriptionMetadata({
        transcription: {
          id: 'abc',
          status: 'stopping',
          room_name: 42,
          extra: 'ignored',
        },
      })
    ).toEqual({
      id: 'abc',
      status: TranscriptionMetadataStatus.Stopping,
      room_name: undefined,
      started_at: undefined,
      started_by: undefined,
    })
  })

  it.each([
    ['no metadata', undefined],
    ['null metadata', null],
    ['a non-object metadata', 'transcription'],
    ['an array metadata', [transcription]],
    ['no transcription key', { recording_status: 'started' }],
    ['a null transcription', { transcription: null }],
    ['a string transcription', { transcription: 'started' }],
    ['a missing id', { transcription: { status: 'started' } }],
    ['an empty id', { transcription: { id: '', status: 'started' } }],
    ['a missing status', { transcription: { id: 'abc' } }],
    ['an unknown status', { transcription: { id: 'abc', status: 'ready' } }],
  ])('returns undefined for %s', (_, metadata) => {
    expect(parseTranscriptionMetadata(metadata)).toBeUndefined()
  })
})

describe('getTranscriptionStatus', () => {
  it('maps a started transcription', () => {
    expect(getTranscriptionStatus({ transcription })).toMatchObject({
      isActive: true,
      isStarted: true,
      isStopping: false,
    })
  })

  it('maps a stopping transcription', () => {
    expect(
      getTranscriptionStatus({
        transcription: { ...transcription, status: 'stopping' },
      })
    ).toMatchObject({
      isActive: true,
      isStarted: false,
      isStopping: true,
    })
  })

  it('is inactive without a valid transcription entry', () => {
    expect(getTranscriptionStatus({ recording_status: 'started' })).toEqual({
      transcription: undefined,
      isActive: false,
      isStarted: false,
      isStopping: false,
    })
  })
})
