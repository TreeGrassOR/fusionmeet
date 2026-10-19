import { useTranslation } from 'react-i18next'
import { MenuItem } from 'react-aria-components'
import type { Participant } from 'livekit-client'
import { RiVolumeMuteLine, RiVolumeUpLine } from '@remixicon/react'
import { HStack } from '@/styled-system/jsx'
import { menuRecipe } from '@/primitives/menuRecipe'
import { toggleParticipantMutedForMe } from '@/stores/participantAudio'
import { useParticipantAudio } from '../hooks/useParticipantAudio'

/**
 * Participant menu item silencing a remote participant for the current user only.
 */
export const MuteForMeMenuItem = ({
  participant,
}: {
  participant: Participant
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
  const { mutedForMe } = useParticipantAudio(participant.identity)
  const name = participant.name || participant.identity

  return (
    <MenuItem
      aria-label={
        mutedForMe
          ? t('unmuteForMe.ariaLabel', { name })
          : t('muteForMe.ariaLabel', { name })
      }
      className={menuRecipe({ icon: true }).item}
      onAction={() => toggleParticipantMutedForMe(participant.identity)}
    >
      <HStack gap={0.25}>
        {mutedForMe ? (
          <>
            <RiVolumeUpLine size={20} aria-hidden />
            {t('unmuteForMe.label')}
          </>
        ) : (
          <>
            <RiVolumeMuteLine size={20} aria-hidden />
            {t('muteForMe.label')}
          </>
        )}
      </HStack>
    </MenuItem>
  )
}
