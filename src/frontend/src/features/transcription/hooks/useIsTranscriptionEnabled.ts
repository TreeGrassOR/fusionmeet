import { useConfig } from '@/api/useConfig'

/**
 * Whether the backend exposes the per-participant transcription
 * (TRANSCRIPTION_ENABLED).
 */
export const useIsTranscriptionEnabled = () => {
  const { data } = useConfig()
  return data?.transcription?.enabled === true
}
