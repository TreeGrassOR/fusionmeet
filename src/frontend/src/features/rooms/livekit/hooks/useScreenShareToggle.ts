import { useCallback, useEffect, useRef } from 'react'
import {
  useRoomContext,
  useTrackToggle,
  type UseTrackToggleProps,
} from '@livekit/components-react'
import { Track } from 'livekit-client'
import { useTranslation } from 'react-i18next'
import { toastQueue } from '@/features/notifications/components/ToastProvider'
import { NotificationType } from '@/features/notifications/NotificationType'
import {
  startScreenShare,
  type ScreenShareAudioDropReason,
} from '../utils/startScreenShare'

/** Long text: react-aria pauses the timer while the toast is hovered. */
const AUDIO_NOT_SHARED_NOTICE_DURATION = 12_000

export const notifyScreenShareAudioNotShared = (
  reason: ScreenShareAudioDropReason
) => {
  toastQueue.add(
    { type: NotificationType.ScreenShareAudioNotShared, reason },
    { timeout: AUDIO_NOT_SHARED_NOTICE_DURATION }
  )
}

type UseScreenShareToggleProps = Pick<
  UseTrackToggleProps<Track.Source.ScreenShare>,
  'onChange' | 'onDeviceError'
>

/**
 * Screen share state and toggle shared by the control bar and the
 * picture-in-picture menu. Starting goes through startScreenShare (shared
 * capture and publish options, conference audio check); errors go to
 * `onDeviceError`, and are ignored without it, like useTrackToggle does.
 */
export const useScreenShareToggle = ({
  onChange,
  onDeviceError,
}: UseScreenShareToggleProps = {}) => {
  const room = useRoomContext()
  // only for the state, which follows the local publications
  const { enabled } = useTrackToggle({
    source: Track.Source.ScreenShare,
    onChange,
  })
  const isPendingRef = useRef(false)
  const onDeviceErrorRef = useRef(onDeviceError)
  useEffect(() => {
    onDeviceErrorRef.current = onDeviceError
  }, [onDeviceError])

  // Load the namespace of the notice before it is needed.
  useTranslation('screenShare', { useSuspense: false })

  const toggle = useCallback(async () => {
    if (isPendingRef.current) return
    isPendingRef.current = true
    const participant = room.localParticipant
    try {
      if (participant.isScreenShareEnabled) {
        await participant.setScreenShareEnabled(false)
      } else {
        const droppedAudio = await startScreenShare(participant)
        if (droppedAudio) notifyScreenShareAudioNotShared(droppedAudio)
      }
    } catch (error) {
      if (error instanceof Error) onDeviceErrorRef.current?.(error)
    } finally {
      isPendingRef.current = false
    }
  }, [room])

  return { enabled, toggle }
}
