import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useConnectionState, useRoomContext } from '@livekit/components-react'
import { ConnectionState } from 'livekit-client'
import { Icon, ToggleButton } from '@/primitives'
import { css } from '@/styled-system/css'
import { type ButtonRecipeProps } from '@/primitives/buttonRecipe'
import { type ToggleButtonProps } from '@/primitives/ToggleButton'
import { useConfig } from '@/api/useConfig'
import { useHumanizeDuration } from '@/hooks/useHumanizeDuration'
import { useRoomId } from '@/features/rooms/livekit/hooks/useRoomId'
import { FeatureFlags } from '@/features/analytics/enums'
import { captureEvent, reportError } from '@/features/analytics/telemetry'
import {
  NotificationType,
  notifyRecordingSaveInProgress,
  useNotifyParticipants,
} from '@/features/notifications'
import {
  RecordingMode,
  useHasRecordingAccess,
  useRecordingEncoding,
  useRecordingStatuses,
} from '@/features/recording'
import { useMutateRecording } from '@/features/recording/hooks/useMutateRecording'

type Props = Pick<NonNullable<ButtonRecipeProps>, 'variant'> & ToggleButtonProps

/**
 * Starts and stops the video recording of the room (the existing
 * "screen_recording" mode), with the same calls, notifications and limits as
 * the recording side panel. Independent from the transcription.
 */
export const RecordingToggle = ({
  variant = 'primaryDark',
  onPress,
  ...props
}: Props) => {
  // No suspense: a namespace loading in the middle of the room would blank
  // the whole app (the only Suspense boundary is at its root).
  const { t, ready } = useTranslation('transcription', {
    keyPrefix: 'recordingToggle',
    useSuspense: false,
  })

  const hasScreenRecordingAccess = useHasRecordingAccess(
    RecordingMode.ScreenRecording,
    FeatureFlags.ScreenRecording
  )

  const { data } = useConfig()
  const formatter = useHumanizeDuration()
  const maxDuration = useMemo(
    () => formatter(data?.recording?.max_duration),
    [data?.recording?.max_duration, formatter]
  )

  const room = useRoomContext()
  const connectionState = useConnectionState(room)
  const roomId = useRoomId()

  const statuses = useRecordingStatuses(RecordingMode.ScreenRecording)
  const { encoding } = useRecordingEncoding()
  const { startRecording, isPendingToStart, stopRecording, isPendingToStop } =
    useMutateRecording()
  const { notifyParticipants } = useNotifyParticipants()

  if (!hasScreenRecordingAccess || !ready) return null

  const isSaving = statuses.isSaving || isPendingToStop
  const isStarting = statuses.isStarting || isPendingToStart

  const tooltip = (() => {
    if (isSaving) return t('saving')
    if (isStarting) return t('starting')
    if (statuses.isStarted) return t('stop')
    if (statuses.isAnotherModeStarted) return t('anotherModeStarted')
    if (maxDuration) return t('startWithLimit', { max_duration: maxDuration })
    return t('start')
  })()

  const isDisabled =
    connectionState !== ConnectionState.Connected ||
    !roomId ||
    isSaving ||
    isStarting ||
    statuses.isAnotherModeStarted

  const handle = async () => {
    if (!roomId) return
    try {
      if (statuses.isStarted) {
        await stopRecording({ id: roomId })
        await notifyParticipants({
          type: NotificationType.ScreenRecordingStopped,
        })
        notifyRecordingSaveInProgress(
          RecordingMode.ScreenRecording,
          room.localParticipant
        )
      } else {
        await startRecording({
          id: roomId,
          mode: RecordingMode.ScreenRecording,
          options: {
            ...(encoding && { encoding }),
          },
        })
        await notifyParticipants({
          type: NotificationType.ScreenRecordingStarted,
        })
        captureEvent('screen-recording-started', {
          from: 'control-bar',
          resolution: encoding?.resolution,
          profile: encoding?.profile,
        })
      }
    } catch (error) {
      reportError('generic_failure', error, {
        context: 'Failed to handle recording:',
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
        isSelected={statuses.isActive || isPendingToStart}
        isDisabled={isDisabled}
        onPress={(e) => {
          void handle()
          onPress?.(e)
        }}
        data-attr={`controls-recording-${statuses.isStarted ? 'stop' : 'start'}`}
      >
        <Icon name="screen_record" />
      </ToggleButton>
    </div>
  )
}
