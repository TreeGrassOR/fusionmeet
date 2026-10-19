import {
  SpinnerIcon,
  Toast,
  useConnectionState,
} from '@livekit/components-react'
import { ConnectionState, type Room } from 'livekit-client'
import { useTranslation } from 'react-i18next'

/**
 * Translated copy of the `ConnectionStateToast` of @livekit/components-react,
 * whose "Connecting", "Reconnecting" and "Disconnected" texts are hard-coded
 * in English. Same markup and class names, so the LiveKit styles still apply.
 */
export const ConnectionStateToast = ({ room }: { room?: Room }) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'connectionState' })
  const state = useConnectionState(room)

  const content = (() => {
    switch (state) {
      case ConnectionState.Reconnecting:
        return (
          <>
            <SpinnerIcon className="lk-spinner" /> {t('reconnecting')}
          </>
        )
      case ConnectionState.Connecting:
        return (
          <>
            <SpinnerIcon className="lk-spinner" /> {t('connecting')}
          </>
        )
      case ConnectionState.Disconnected:
        return t('disconnected')
      default:
        return null
    }
  })()

  if (!content) return null
  return <Toast className="lk-toast-connection-state">{content}</Toast>
}
