import { useIsAdminOrOwner } from '@/features/rooms/livekit/hooks/useIsAdminOrOwner'
import { useIsRecordingModeEnabled } from '@/features/recording/hooks/useIsRecordingModeEnabled'
import { RecordingMode } from '@/features/recording/types'
import { getTranscriptionSettingsVisibility } from '../utils/transcriptionSettings'
import { useCanTranscribe } from './useCanTranscribe'
import { useIsTranscriptionEnabled } from './useIsTranscriptionEnabled'

/**
 * What the "Transcription" tab of the settings dialog shows, and whether the
 * tab shows at all. Must be used inside a LiveKit room.
 */
export const useTranscriptionSettingsVisibility = () => {
  const isAdminOrOwner = useIsAdminOrOwner()
  const isTranscriptModeEnabled = useIsRecordingModeEnabled(
    RecordingMode.Transcript
  )
  const isTranscriptionEnabled = useIsTranscriptionEnabled()
  const canTranscribe = useCanTranscribe()

  return getTranscriptionSettingsVisibility({
    isAdminOrOwner,
    isTranscriptModeEnabled: !!isTranscriptModeEnabled,
    isTranscriptionEnabled,
    canTranscribe,
  })
}
