import { useTranslation } from 'react-i18next'
import { Dialog, DialogTrigger } from 'react-aria-components'
import { FocusScope } from 'react-aria'
import type { Participant } from 'livekit-client'
import { RiVolumeMuteLine, RiVolumeUpLine } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { Box, Button } from '@/primitives'
import { StyledPopover } from '@/primitives/StyledPopover'
import { toggleParticipantMutedForMe } from '@/stores/participantAudio'
import { useParticipantAudio } from '../hooks/useParticipantAudio'
import { ParticipantVolumeSlider } from './ParticipantVolumeSlider'
import { VolumeIcon } from './VolumeIcon'

/**
 * Participant tile button opening a popover with the volume slider and the
 * "mute for me" toggle. `onOpenChange` lets the tile overlay stay visible
 * while the popover is open (the overlay unmounts its buttons when idle).
 */
export const ParticipantVolumeButton = ({
  participant,
  onOpenChange,
}: {
  participant: Participant
  onOpenChange?: (isOpen: boolean) => void
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
  const { mutedForMe, playback } = useParticipantAudio(participant.identity)
  const name = participant.name || participant.identity
  const label = t('tileButton', { name })

  return (
    <DialogTrigger onOpenChange={onOpenChange}>
      <Button
        size="sm"
        variant="primaryTextDark"
        square
        aria-label={label}
        tooltip={label}
      >
        <VolumeIcon playback={playback.microphone} />
      </Button>
      <StyledPopover>
        <Dialog
          aria-label={t('settingsFor', { name })}
          className={css({ outline: 'none' })}
        >
          {/* eslint-disable-next-line jsx-a11y/no-autofocus -- focus the slider when the popover opens */}
          <FocusScope autoFocus>
            <Box size="sm" type="popover" variant="dark">
              <VStack
                alignItems="stretch"
                gap={0.5}
                width="16rem"
                maxWidth="100%"
              >
                <ParticipantVolumeSlider
                  participant={participant}
                  variant="dark"
                />
                <Button
                  size="sm"
                  variant="primaryTextDark"
                  fullWidth
                  aria-label={
                    mutedForMe
                      ? t('unmuteForMe.ariaLabel', { name })
                      : t('muteForMe.ariaLabel', { name })
                  }
                  onPress={() =>
                    toggleParticipantMutedForMe(participant.identity)
                  }
                >
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
                </Button>
              </VStack>
            </Box>
          </FocusScope>
        </Dialog>
      </StyledPopover>
    </DialogTrigger>
  )
}
