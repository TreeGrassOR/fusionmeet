import { useUser } from '@/features/auth/api/useUser'
import { useIsAdminOrOwner } from '@/features/rooms/livekit/hooks/useIsAdminOrOwner'
import { useIsRecordingOpenToParticipants } from '@/features/recording/hooks/useCanRecord'

/**
 * Whether the local participant may start and stop the transcription.
 *
 * Mirrors useCanRecord: the backend checks the same permission as for
 * recordings (CanManageRecording), i.e. room owners and administrators, plus
 * logged-in participants when recording is open to them on public or
 * trusted rooms. Must be used inside a LiveKit room.
 */
export const useCanTranscribe = () => {
  const isAdminOrOwner = useIsAdminOrOwner()
  const { isLoggedIn } = useUser()
  const isRecordingOpenToParticipants = useIsRecordingOpenToParticipants()

  return (
    isAdminOrOwner || (isLoggedIn === true && isRecordingOpenToParticipants)
  )
}
