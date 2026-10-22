import { useTranslation } from 'react-i18next'
import type { Participant } from 'livekit-client'
import { RiVolumeMuteFill } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { useParticipantAudio } from '../hooks/useParticipantAudio'

/**
 * Tile badge shown while a remote participant is silenced for the current user.
 */
export const MutedForMeIndicator = ({
  participant,
}: {
  participant: Participant
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
  const { playback } = useParticipantAudio(participant.identity)

  if (participant.isLocal || !playback.microphone.isMuted) return null

  return (
    <div
      role="img"
      aria-label={t('mutedForMeIndicator')}
      title={t('mutedForMeIndicator')}
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
