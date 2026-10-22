import { useTranslation } from 'react-i18next'
import type { Participant } from 'livekit-client'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { useHasScreenShareAudio } from '../hooks/useHasScreenShareAudio'
import { ParticipantVolumeSlider } from './ParticipantVolumeSlider'

/**
 * Volume sliders rendered in the participant menu popover, below (and outside
 * of) the react-aria menu: menu items take arrow keys and close on action,
 * which does not suit a slider. Tab moves from the menu to the sliders.
 * The second slider, for the screen share audio, shows while it is shared.
 */
export const ParticipantMenuVolumeSection = ({
  participant,
}: {
  participant: Participant
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
  const hasScreenShareAudio = useHasScreenShareAudio(participant)
  const name = participant.name || participant.identity

  return (
    <div
      role="group"
      aria-label={t('settingsFor', { name })}
      className={css({
        width: '14rem',
        maxWidth: '100%',
        marginTop: 0.25,
        paddingTop: 0.5,
        paddingX: 0.5,
        paddingBottom: 0.25,
        borderTop: '1px solid {colors.box.border}',
      })}
    >
      <VStack alignItems="stretch" gap={0.75}>
        <ParticipantVolumeSlider participant={participant} />
        {hasScreenShareAudio && (
          <ParticipantVolumeSlider
            participant={participant}
            source="screenShareAudio"
          />
        )}
      </VStack>
    </div>
  )
}
