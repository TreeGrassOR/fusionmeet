import { useTranslation } from 'react-i18next'
import type { Participant } from 'livekit-client'
import { Slider } from '@/primitives'
import { setParticipantVolume } from '@/stores/participantAudio'
import { useParticipantAudio } from '../hooks/useParticipantAudio'

const VOLUME_STEP = 0.05

const PERCENT_FORMAT: Intl.NumberFormatOptions = {
  style: 'percent',
  maximumFractionDigits: 0,
}

export const ParticipantVolumeSlider = ({
  participant,
  variant,
}: {
  participant: Participant
  variant?: 'light' | 'dark'
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
  const { volume, maxVolume } = useParticipantAudio(participant.identity)

  return (
    <Slider
      label={t('volume.label')}
      variant={variant}
      minValue={0}
      maxValue={maxVolume}
      step={VOLUME_STEP}
      value={Math.min(volume, maxVolume)}
      formatOptions={PERCENT_FORMAT}
      onChange={(value) => setParticipantVolume(participant.identity, value)}
    />
  )
}
