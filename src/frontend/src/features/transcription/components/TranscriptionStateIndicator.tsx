import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { Icon, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { useScreenReaderAnnounce } from '@/hooks/useScreenReaderAnnounce'
import { RecordingMode, useRecordingStatuses } from '@/features/recording'
import { useTranscriptionStatus } from '../hooks/useTranscriptionStatus'

// The recording banner (RecordingStateToast) is fixed at the same corner:
// when it is shown, this indicator is stacked right below it.
const TOP_OFFSET = '10px'
const TOP_OFFSET_BELOW_RECORDING = '52px'

/**
 * Same visibility rule as RecordingStateToast: shown while a recording is
 * starting or started, whatever its mode.
 */
const useIsRecordingBannerVisible = () => {
  const screenRecording = useRecordingStatuses(RecordingMode.ScreenRecording)
  const transcript = useRecordingStatuses(RecordingMode.Transcript)
  return (
    screenRecording.isStarting ||
    screenRecording.isStarted ||
    transcript.isStarting ||
    transcript.isStarted
  )
}

/**
 * Tells every participant, late joiners included, that the meeting is being
 * transcribed. Read from the room metadata.
 */
export const TranscriptionStateIndicator = () => {
  const { t, ready } = useTranslation('transcription', {
    keyPrefix: 'indicator',
    useSuspense: false,
  })
  const { isActive, isStopping } = useTranscriptionStatus()
  const isRecordingBannerVisible = useIsRecordingBannerVisible()

  const announce = useScreenReaderAnnounce()
  const lastLabelRef = useRef('')

  // Static keys, so the i18n extractor keeps them.
  const label = isActive ? (isStopping ? t('stopping') : t('started')) : ''

  // Announce only actual changes, not re-renders.
  useEffect(() => {
    if (!ready || label === lastLabelRef.current) return
    lastLabelRef.current = label
    if (label) announce(label)
  }, [announce, label, ready])

  if (!label || !ready) return null

  return (
    <div
      data-attr="transcription-state-indicator"
      className={css({
        display: 'flex',
        alignItems: 'center',
        position: 'fixed',
        left: '10px',
        paddingY: '0.25rem',
        paddingX: '0.75rem',
        backgroundColor: 'primary.800',
        border: '1px solid',
        borderColor: 'white',
        color: 'white',
        borderRadius: '4px',
        gap: '0.5rem',
      })}
      style={{
        top: isRecordingBannerVisible ? TOP_OFFSET_BELOW_RECORDING : TOP_OFFSET,
      }}
    >
      {isStopping ? (
        <Spinner size={20} variant="dark" />
      ) : (
        <Icon name="speech_to_text" />
      )}
      <Text
        variant="sm"
        className={css({
          fontWeight: '500 !important',
        })}
      >
        {label}
      </Text>
    </div>
  )
}
