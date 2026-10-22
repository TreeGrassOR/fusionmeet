import { useTranslation } from 'react-i18next'
import type { Participant } from 'livekit-client'
import { RiVolumeMuteFill } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { useParticipantAudio } from '../hooks/useParticipantAudio'
import { useHasScreenShareAudio } from '../hooks/useHasScreenShareAudio'

/**
 * Screen share tile badge shown while the shared audio of a remote
 * participant is silenced for the current user.
 */
export const ScreenShareMutedIndicator = ({
  participant,
}: {
  participant: Participant
}) => {
  const { t } = useTranslation('participantAudio', {
    keyPrefix: 'screenShare',
    useSuspense: false,
  })
  const { playback } = useParticipantAudio(participant.identity)
  const hasAudio = useHasScreenShareAudio(participant)

  if (participant.isLocal || !hasAudio || !playback.screenShareAudio.isMuted) {
    return null
  }

  return (
    <div
      role="img"
      aria-label={t('mutedIndicator')}
      title={t('mutedIndicator')}
      className={css({
        backgroundColor: 'primaryDark.300',
        borderRadius: '4px',
        padding: 0.25,
      })}
    >
      <RiVolumeMuteFill size={16} color="white" aria-hidden />
    </div>
  )
}
