import { TranscriptionStateIndicator } from './TranscriptionStateIndicator'
import { TranscriptionErrorDialog } from './TranscriptionErrorDialog'

/**
 * Room-wide transcription UI, mounted once in the conference next to the
 * RecordingProvider.
 */
export const TranscriptionProvider = () => {
  return (
    <>
      <TranscriptionStateIndicator />
      <TranscriptionErrorDialog />
    </>
  )
}
