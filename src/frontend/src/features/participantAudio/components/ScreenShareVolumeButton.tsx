import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogTrigger } from 'react-aria-components'
import { FocusScope } from 'react-aria'
import type { Participant } from 'livekit-client'
import { RiVolumeMuteLine, RiVolumeUpLine } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { VStack } from '@/styled-system/jsx'
import { Box, Button, Text } from '@/primitives'
import { StyledPopover } from '@/primitives/StyledPopover'
import { toggleParticipantScreenShareMuted } from '@/stores/participantAudio'
import { useParticipantAudio } from '../hooks/useParticipantAudio'
import { useHasScreenShareAudio } from '../hooks/useHasScreenShareAudio'
import { ParticipantVolumeSlider } from './ParticipantVolumeSlider'
import { VolumeIcon } from './VolumeIcon'

/**
 * Screen share tile button opening a popover with the volume of the shared
 * audio and its own mute, independent from the participant's voice. Only
 * rendered while the participant shares audio.
 */
export const ScreenShareVolumeButton = ({
  participant,
  onOpenChange,
}: {
  participant: Participant
  onOpenChange?: (isOpen: boolean) => void
}) => {
  const { t } = useTranslation('participantAudio', {
    keyPrefix: 'screenShare',
    useSuspense: false,
  })
  const hasAudio = useHasScreenShareAudio(participant)
  const { mutedForMe, screenShareMuted, playback } = useParticipantAudio(
    participant.identity
  )
  const name = participant.name || participant.identity
  const label = t('tileButton', { name })

  // the popover unmounts without a close event when the audio goes away
  useEffect(() => {
    if (!hasAudio) onOpenChange?.(false)
  }, [hasAudio, onOpenChange])

  if (!hasAudio) return null

  return (
    <DialogTrigger onOpenChange={onOpenChange}>
      <Button
        size="sm"
        variant="primaryTextDark"
        square
        aria-label={label}
        tooltip={label}
      >
        <VolumeIcon playback={playback.screenShareAudio} />
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
                  source="screenShareAudio"
                />
                <Button
                  size="sm"
                  variant="primaryTextDark"
                  fullWidth
                  aria-label={
                    screenShareMuted
                      ? t('unmute.ariaLabel', { name })
                      : t('mute.ariaLabel', { name })
                  }
                  onPress={() =>
                    toggleParticipantScreenShareMuted(participant.identity)
                  }
                >
                  {screenShareMuted ? (
                    <>
                      <RiVolumeUpLine size={20} aria-hidden />
                      {t('unmute.label')}
                    </>
                  ) : (
                    <>
                      <RiVolumeMuteLine size={20} aria-hidden />
                      {t('mute.label')}
                    </>
                  )}
                </Button>
                {mutedForMe && (
                  <Text variant="sm" margin={false}>
                    {t('mutedWithParticipant')}
                  </Text>
                )}
              </VStack>
            </Box>
          </FocusScope>
        </Dialog>
      </StyledPopover>
    </DialogTrigger>
  )
}
