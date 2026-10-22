import { type RemoteParticipant, Track } from 'livekit-client'
import type {
  ParticipantPlayback,
  SourcePlayback,
} from '@/stores/participantAudio'

/**
 * Audio sources of a participant the settings apply to, each with its own
 * playback (livekit-client keeps one volume per source).
 */
export const PARTICIPANT_AUDIO_SOURCES = [
  Track.Source.Microphone,
  Track.Source.ScreenShareAudio,
] as const

type ParticipantAudioSource = (typeof PARTICIPANT_AUDIO_SOURCES)[number]

const PLAYBACK_KEY: Record<ParticipantAudioSource, keyof ParticipantPlayback> =
  {
    [Track.Source.Microphone]: 'microphone',
    [Track.Source.ScreenShareAudio]: 'screenShareAudio',
  }

const getSourcePlayback = (
  playback: ParticipantPlayback,
  source: ParticipantAudioSource
): SourcePlayback => playback[PLAYBACK_KEY[source]]

/**
 * Volume used while a participant is silenced. livekit-client 2.21 only
 * re-applies a truthy volume when a track element gets (re)attached, a 0 would
 * be lost. This gain is inaudible.
 */
export const SILENT_VOLUME = 0.0001

const isParticipantAudioSource = (
  source: Track.Source
): source is ParticipantAudioSource =>
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
  playback: ParticipantPlayback
) => {
  for (const source of PARTICIPANT_AUDIO_SOURCES) {
    const { volume, isMuted } = getSourcePlayback(playback, source)
    const targetVolume = isMuted ? SILENT_VOLUME : volume
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
    const { source } = publication
    if (!isParticipantAudioSource(source) || !publication.isDesired) return
    const { isMuted } = getSourcePlayback(playback, source)
    if (publication.isEnabled === isMuted) publication.setEnabled(!isMuted)
  })
}
