import { useParticipantTracks } from '@livekit/components-react'
import { Track, type Participant } from 'livekit-client'

const SCREEN_SHARE_AUDIO = [Track.Source.ScreenShareAudio]

/** Whether the participant currently publishes screen share audio. */
export const useHasScreenShareAudio = (participant: Participant): boolean =>
  useParticipantTracks(SCREEN_SHARE_AUDIO, participant.identity).length > 0
