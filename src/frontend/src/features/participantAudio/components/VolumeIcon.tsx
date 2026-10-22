import {
  RiVolumeDownLine,
  RiVolumeMuteLine,
  RiVolumeUpLine,
} from '@remixicon/react'
import type { SourcePlayback } from '@/stores/participantAudio'

/** Icon of a volume button, following the playback of its source. */
export const VolumeIcon = ({
  playback: { isMuted, volume },
}: {
  playback: SourcePlayback
}) => {
  if (isMuted) return <RiVolumeMuteLine aria-hidden />
  if (volume < 1) return <RiVolumeDownLine aria-hidden />
  return <RiVolumeUpLine aria-hidden />
}
