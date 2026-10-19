import { useMemo } from 'react'
import { useRoomMetadata } from '@/features/recording/hooks/useRoomMetadata'
import { getTranscriptionStatus } from '../utils/transcriptionMetadata'

/**
 * Status of the room transcription, read from the room metadata so every
 * participant, including late joiners, gets the same value.
 * Must be used inside a LiveKit room.
 */
export const useTranscriptionStatus = () => {
  const metadata = useRoomMetadata()
  return useMemo(() => getTranscriptionStatus(metadata), [metadata])
}
