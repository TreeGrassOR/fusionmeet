import { type RemoteParticipant, Track } from 'livekit-client'
import type { ParticipantPlayback } from '@/stores/participantAudio'

/**
 * Settings apply to every audio source of the participant.
 */
export const PARTICIPANT_AUDIO_SOURCES = [
  Track.Source.Microphone,
  Track.Source.ScreenShareAudio,
] as const

/**
 * Volume used while a participant is silenced. livekit-client 2.21 only
 * re-applies a truthy volume when a track element gets (re)attached, a 0 would
 * be lost. This gain is inaudible.
 */
export const SILENT_VOLUME = 0.0001

const isParticipantAudioSource = (source: Track.Source) =>
  (PARTICIPANT_AUDIO_SOURCES as readonly Track.Source[]).includes(source)

/**
 * Applies the local playback settings to a remote participant.
 *
 * Silencing also disables the subscribed publications: the server stops
 * forwarding them, which, unlike a volume of 0, survives re-subscriptions.
 * Idempotent and cheap: safe to call on every store change or room event.
 */
export const applyParticipantPlayback = (
  participant: RemoteParticipant,
  { volume, isMuted }: ParticipantPlayback
) => {
  const targetVolume = isMuted ? SILENT_VOLUME : volume
  for (const source of PARTICIPANT_AUDIO_SOURCES) {
    try {
      participant.setVolume(targetVolume, source)
    } catch {
      // HTMLMediaElement.volume throws above 1 when the room has no AudioContext
      participant.setVolume(Math.min(targetVolume, 1), source)
    }
  }

  // Not getTrackPublication(source): a republished track is announced while
  // the previous publication of the same source is still listed, and only the
  // first one would be returned, leaving the new one enabled.
  participant.audioTrackPublications.forEach((publication) => {
    if (
      isParticipantAudioSource(publication.source) &&
      publication.isDesired &&
      publication.isEnabled === isMuted
    ) {
      publication.setEnabled(!isMuted)
    }
  })
}
