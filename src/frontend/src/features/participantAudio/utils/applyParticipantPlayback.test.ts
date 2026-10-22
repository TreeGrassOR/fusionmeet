import { describe, expect, it, vi } from 'vitest'
import { type RemoteParticipant, Track } from 'livekit-client'
import type {
  ParticipantPlayback,
  SourcePlayback,
} from '@/stores/participantAudio'
import {
  applyParticipantPlayback,
  SILENT_VOLUME,
} from './applyParticipantPlayback'

/** Same playback for both sources. */
const both = (volume: number, isMuted: boolean): ParticipantPlayback => ({
  microphone: { volume, isMuted },
  screenShareAudio: { volume, isMuted },
})

const playback = (
  microphone: SourcePlayback,
  screenShareAudio: SourcePlayback
): ParticipantPlayback => ({ microphone, screenShareAudio })

type FakePublication = {
  source: Track.Source
  isDesired: boolean
  isEnabled: boolean
  setEnabled: (enabled: boolean) => void
}

const createPublication = (
  source: Track.Source,
  isDesired = true
): FakePublication => {
  const publication: FakePublication = {
    source,
    isDesired,
    isEnabled: true,
    setEnabled: vi.fn((enabled: boolean) => {
      publication.isEnabled = enabled
    }),
  }
  return publication
}

const createParticipant = ({
  publications = [],
  maxElementVolume = Number.POSITIVE_INFINITY,
}: {
  publications?: FakePublication[]
  maxElementVolume?: number
} = {}) => {
  const volumes = new Map<Track.Source, number>()
  const participant = {
    setVolume: vi.fn((volume: number, source: Track.Source) => {
      if (volume > maxElementVolume) throw new Error('IndexSizeError')
      volumes.set(source, volume)
    }),
    audioTrackPublications: new Map(
      publications.map((publication, index) => [`TR_${index}`, publication])
    ),
  }
  return {
    participant: participant as unknown as RemoteParticipant,
    volumes,
  }
}

describe('applyParticipantPlayback', () => {
  it('sets the volume of the microphone and screen share audio separately', () => {
    const { participant, volumes } = createParticipant()
    applyParticipantPlayback(
      participant,
      playback({ volume: 1.5, isMuted: false }, { volume: 0.4, isMuted: false })
    )
    expect(volumes.get(Track.Source.Microphone)).toBe(1.5)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(0.4)
  })

  it('mutes the screen share audio and keeps the microphone audible', () => {
    const microphone = createPublication(Track.Source.Microphone)
    const screenShareAudio = createPublication(Track.Source.ScreenShareAudio)
    const { participant, volumes } = createParticipant({
      publications: [microphone, screenShareAudio],
    })

    applyParticipantPlayback(
      participant,
      playback({ volume: 0.8, isMuted: false }, { volume: 1.2, isMuted: true })
    )
    expect(microphone.setEnabled).not.toHaveBeenCalled()
    expect(microphone.isEnabled).toBe(true)
    expect(volumes.get(Track.Source.Microphone)).toBe(0.8)
    expect(screenShareAudio.setEnabled).toHaveBeenCalledWith(false)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(SILENT_VOLUME)

    // unmuting restores the kept volume
    applyParticipantPlayback(
      participant,
      playback({ volume: 0.8, isMuted: false }, { volume: 1.2, isMuted: false })
    )
    expect(screenShareAudio.isEnabled).toBe(true)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(1.2)
  })

  it('mutes the microphone and keeps the screen share audio audible', () => {
    const microphone = createPublication(Track.Source.Microphone)
    const screenShareAudio = createPublication(Track.Source.ScreenShareAudio)
    const { participant, volumes } = createParticipant({
      publications: [microphone, screenShareAudio],
    })

    applyParticipantPlayback(
      participant,
      playback({ volume: 0, isMuted: true }, { volume: 1, isMuted: false })
    )
    expect(microphone.isEnabled).toBe(false)
    expect(screenShareAudio.setEnabled).not.toHaveBeenCalled()
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(1)
  })

  it('does not touch publications that do not need it', () => {
    const microphone = createPublication(Track.Source.Microphone)
    const { participant } = createParticipant({ publications: [microphone] })
    applyParticipantPlayback(participant, both(1, false))
    expect(microphone.setEnabled).not.toHaveBeenCalled()
  })

  it('disables the publications and uses a non-zero silent volume when muted', () => {
    const microphone = createPublication(Track.Source.Microphone)
    const screenShareAudio = createPublication(Track.Source.ScreenShareAudio)
    const { participant, volumes } = createParticipant({
      publications: [microphone, screenShareAudio],
    })

    applyParticipantPlayback(participant, both(0.8, true))
    expect(microphone.setEnabled).toHaveBeenCalledWith(false)
    expect(screenShareAudio.setEnabled).toHaveBeenCalledWith(false)
    expect(volumes.get(Track.Source.Microphone)).toBe(SILENT_VOLUME)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(SILENT_VOLUME)
    expect(SILENT_VOLUME).toBeGreaterThan(0)

    // idempotent
    applyParticipantPlayback(participant, both(0.8, true))
    expect(microphone.setEnabled).toHaveBeenCalledTimes(1)
    expect(screenShareAudio.setEnabled).toHaveBeenCalledTimes(1)

    applyParticipantPlayback(participant, both(0.8, false))
    expect(microphone.setEnabled).toHaveBeenLastCalledWith(true)
    expect(microphone.isEnabled).toBe(true)
    expect(volumes.get(Track.Source.Microphone)).toBe(0.8)
  })

  it('disables every publication of a source, e.g. while one is republished', () => {
    // a republished track is announced before the previous one is removed
    const previous = createPublication(Track.Source.Microphone)
    const republished = createPublication(Track.Source.Microphone)
    const { participant } = createParticipant({
      publications: [previous, republished],
    })
    applyParticipantPlayback(participant, both(1, true))
    expect(previous.isEnabled).toBe(false)
    expect(republished.isEnabled).toBe(false)
  })

  it('leaves other audio sources alone', () => {
    const unknown = createPublication(Track.Source.Unknown)
    const { participant } = createParticipant({ publications: [unknown] })
    applyParticipantPlayback(participant, both(1, true))
    expect(unknown.setEnabled).not.toHaveBeenCalled()
  })

  it('skips publications the user does not subscribe to', () => {
    const microphone = createPublication(Track.Source.Microphone, false)
    const { participant } = createParticipant({ publications: [microphone] })
    applyParticipantPlayback(participant, both(1, true))
    expect(microphone.setEnabled).not.toHaveBeenCalled()
  })

  it('falls back to 100 % when the browser cannot amplify', () => {
    const { participant, volumes } = createParticipant({ maxElementVolume: 1 })
    applyParticipantPlayback(
      participant,
      playback({ volume: 2, isMuted: false }, { volume: 1.5, isMuted: false })
    )
    expect(volumes.get(Track.Source.Microphone)).toBe(1)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(1)
  })
})
