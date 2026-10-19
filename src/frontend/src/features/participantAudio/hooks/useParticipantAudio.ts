import { useMaybeRoomContext } from '@livekit/components-react'
import { useSnapshot } from 'valtio'
import {
  getMaxParticipantVolume,
  getParticipantAudioSettings,
  participantAudioStore,
  resolveParticipantPlayback,
} from '@/stores/participantAudio'

/**
 * Highest volume the slider offers: 200 % when the room mixes audio through
 * Web Audio (see isVolumeBoostSupported), 100 % otherwise.
 */
export const useMaxParticipantVolume = (): number => {
  const room = useMaybeRoomContext()
  return getMaxParticipantVolume(!!room?.options.webAudioMix)
}

export const useParticipantAudio = (identity: string) => {
  const snap = useSnapshot(participantAudioStore)
  const maxVolume = useMaxParticipantVolume()
  const settings = getParticipantAudioSettings(snap, identity)
  return {
    ...settings,
    maxVolume,
    playback: resolveParticipantPlayback(settings, maxVolume),
  }
}
