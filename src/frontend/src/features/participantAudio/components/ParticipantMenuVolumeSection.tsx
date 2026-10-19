import { useTranslation } from 'react-i18next'
import type { Participant } from 'livekit-client'
import { css } from '@/styled-system/css'
import { ParticipantVolumeSlider } from './ParticipantVolumeSlider'

/**
 * Volume slider rendered in the participant menu popover, below (and outside
 * of) the react-aria menu: menu items take arrow keys and close on action,
 * which does not suit a slider. Tab moves from the menu to the slider.
 */
export const ParticipantMenuVolumeSection = ({
  participant,
}: {
  participant: Participant
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
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
      <ParticipantVolumeSlider participant={participant} />
    </div>
  )
}
