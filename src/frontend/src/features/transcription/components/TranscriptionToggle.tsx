import { useTranslation } from 'react-i18next'
import { useConnectionState, useRoomContext } from '@livekit/components-react'
import { ConnectionState } from 'livekit-client'
import { Icon, ToggleButton } from '@/primitives'
import { css } from '@/styled-system/css'
import { type ButtonRecipeProps } from '@/primitives/buttonRecipe'
import { type ToggleButtonProps } from '@/primitives/ToggleButton'
import { useRoomId } from '@/features/rooms/livekit/hooks/useRoomId'
import {
  NotificationType,
  useNotifyParticipants,
} from '@/features/notifications'
import { captureEvent, reportError } from '@/features/analytics/telemetry'
import { useIsTranscriptionEnabled } from '../hooks/useIsTranscriptionEnabled'
import { useCanTranscribe } from '../hooks/useCanTranscribe'
import { useTranscriptionStatus } from '../hooks/useTranscriptionStatus'
import {
  isExpectedTranscriptionError,
  useMutateTranscription,
} from '../hooks/useMutateTranscription'

type Props = Pick<NonNullable<ButtonRecipeProps>, 'variant'> & ToggleButtonProps

/**
 * Starts and stops the per-participant transcription of the room, for the
 * participants allowed to (owner, administrators). Independent from the
 * recording: both can run at the same time.
 */
export const TranscriptionToggle = ({
  variant = 'primaryDark',
  onPress,
  ...props
}: Props) => {
  // No suspense: a namespace loading in the middle of the room would blank
  // the whole app (the only Suspense boundary is at its root).
  const { t, ready } = useTranslation('transcription', {
    keyPrefix: 'toggle',
    useSuspense: false,
  })

  const isEnabled = useIsTranscriptionEnabled()
  const canTranscribe = useCanTranscribe()

  const room = useRoomContext()
  const connectionState = useConnectionState(room)
  const roomId = useRoomId()

  const { isStarted, isStopping } = useTranscriptionStatus()
  const {
    startTranscription,
    isPendingToStart,
    stopTranscription,
    isPendingToStop,
  } = useMutateTranscription()
  const { notifyParticipants } = useNotifyParticipants()

  if (!isEnabled || !canTranscribe || !ready) return null

  const isPending = isPendingToStart || isPendingToStop
  const isSelected = isStarted || isStopping || isPendingToStart

  const tooltip = (() => {
    if (isPendingToStart) return t('starting')
    if (isStopping || isPendingToStop) return t('stopping')
    if (isStarted) return t('stop')
    return t('start')
  })()

  // Not disabled while stopping: pressing then tries a new start, which the
  // backend accepts once a stuck "stopping" transcription has timed out.
  const isDisabled =
    connectionState !== ConnectionState.Connected || !roomId || isPending

  const handle = async () => {
    if (!roomId) return
    try {
      if (isStarted) {
        await stopTranscription({ id: roomId })
        await notifyParticipants({
          type: NotificationType.TranscriptionStopped,
        })
        captureEvent('transcription-stopped', {})
      } else {
        await startTranscription({ id: roomId })
        await notifyParticipants({
          type: NotificationType.TranscriptionStarted,
        })
        captureEvent('transcription-started', {})
      }
    } catch (error) {
      if (isExpectedTranscriptionError(error)) return
      reportError('generic_failure', error, {
        context: 'Failed to handle transcription:',
      })
    }
  }

  return (
    <div
      className={css({
        position: 'relative',
        display: 'inline-block',
      })}
    >
      <ToggleButton
        {...props}
        square
        variant={variant}
        aria-label={tooltip}
        tooltip={tooltip}
        isSelected={isSelected}
        isDisabled={isDisabled}
        onPress={(e) => {
          void handle()
          onPress?.(e)
        }}
        data-attr={`controls-transcription-${isStarted ? 'stop' : 'start'}`}
      >
        <Icon name="speech_to_text" />
      </ToggleButton>
    </div>
  )
}
