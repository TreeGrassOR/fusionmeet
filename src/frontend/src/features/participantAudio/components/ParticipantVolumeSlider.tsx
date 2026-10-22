import { useTranslation } from 'react-i18next'
import type { Participant } from 'livekit-client'
import { Slider } from '@/primitives'
import {
  setParticipantScreenShareVolume,
  setParticipantVolume,
} from '@/stores/participantAudio'
import { useParticipantAudio } from '../hooks/useParticipantAudio'

const VOLUME_STEP = 0.05

const PERCENT_FORMAT: Intl.NumberFormatOptions = {
  style: 'percent',
  maximumFractionDigits: 0,
}

export type ParticipantVolumeSource = 'microphone' | 'screenShareAudio'

export const ParticipantVolumeSlider = ({
  participant,
  variant,
  source = 'microphone',
}: {
  participant: Participant
  variant?: 'light' | 'dark'
  source?: ParticipantVolumeSource
}) => {
  const { t } = useTranslation('participantAudio', { useSuspense: false })
  const { volume, screenShareVolume, maxVolume } = useParticipantAudio(
    participant.identity
  )
  const isScreenShare = source === 'screenShareAudio'
  const value = isScreenShare ? screenShareVolume : volume
  const setVolume = isScreenShare
    ? setParticipantScreenShareVolume
    : setParticipantVolume

  return (
    <Slider
      label={t(isScreenShare ? 'screenShare.volume.label' : 'volume.label')}
      variant={variant}
      minValue={0}
      maxValue={maxVolume}
      step={VOLUME_STEP}
      value={Math.min(value, maxVolume)}
      formatOptions={PERCENT_FORMAT}
      onChange={(next) => setVolume(participant.identity, next)}
    />
  )
}
