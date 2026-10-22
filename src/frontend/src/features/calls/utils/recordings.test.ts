import { describe, expect, it } from 'vitest'
import { RecordingStatus } from '@/features/recording/types'
import { getRecordingState } from './recordings'

describe('getRecordingState', () => {
  it('is ready for every recording the download page offers', () => {
    expect(
      [
        RecordingStatus.Saved,
        RecordingStatus.NotificationSucceed,
        RecordingStatus.ExternalProcessSuccessful,
        // the file exists although a step after it failed
        RecordingStatus.FailedToStop,
        RecordingStatus.ExternalProcessFailed,
      ].map(getRecordingState)
    ).toEqual(['ready', 'ready', 'ready', 'ready', 'ready'])
  })

  it('tells running, processing and failed recordings apart', () => {
    expect(getRecordingState(RecordingStatus.Initiated)).toBe('inProgress')
    expect(getRecordingState(RecordingStatus.Active)).toBe('inProgress')
    expect(getRecordingState(RecordingStatus.Stopped)).toBe('processing')
    expect(getRecordingState(RecordingStatus.Failed)).toBe('failed')
    expect(getRecordingState(RecordingStatus.FailedToStart)).toBe('failed')
    expect(getRecordingState(RecordingStatus.Aborted)).toBe('failed')
  })
})
