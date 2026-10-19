import { describe, expect, it, vi } from 'vitest'
import { type RemoteParticipant, Track } from 'livekit-client'
import {
  applyParticipantPlayback,
  SILENT_VOLUME,
} from './applyParticipantPlayback'

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
  it('sets the volume of the microphone and screen share audio', () => {
    const { participant, volumes } = createParticipant()
    applyParticipantPlayback(participant, { volume: 1.5, isMuted: false })
    expect(volumes.get(Track.Source.Microphone)).toBe(1.5)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(1.5)
  })

  it('does not touch publications that do not need it', () => {
    const microphone = createPublication(Track.Source.Microphone)
    const { participant } = createParticipant({ publications: [microphone] })
    applyParticipantPlayback(participant, { volume: 1, isMuted: false })
    expect(microphone.setEnabled).not.toHaveBeenCalled()
  })

  it('disables the publications and uses a non-zero silent volume when muted', () => {
    const microphone = createPublication(Track.Source.Microphone)
    const screenShareAudio = createPublication(Track.Source.ScreenShareAudio)
    const { participant, volumes } = createParticipant({
      publications: [microphone, screenShareAudio],
    })

    applyParticipantPlayback(participant, { volume: 0.8, isMuted: true })
    expect(microphone.setEnabled).toHaveBeenCalledWith(false)
    expect(screenShareAudio.setEnabled).toHaveBeenCalledWith(false)
    expect(volumes.get(Track.Source.Microphone)).toBe(SILENT_VOLUME)
    expect(SILENT_VOLUME).toBeGreaterThan(0)

    // idempotent
    applyParticipantPlayback(participant, { volume: 0.8, isMuted: true })
    expect(microphone.setEnabled).toHaveBeenCalledTimes(1)

    applyParticipantPlayback(participant, { volume: 0.8, isMuted: false })
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
    applyParticipantPlayback(participant, { volume: 1, isMuted: true })
    expect(previous.isEnabled).toBe(false)
    expect(republished.isEnabled).toBe(false)
  })

  it('leaves other audio sources alone', () => {
    const unknown = createPublication(Track.Source.Unknown)
    const { participant } = createParticipant({ publications: [unknown] })
    applyParticipantPlayback(participant, { volume: 1, isMuted: true })
    expect(unknown.setEnabled).not.toHaveBeenCalled()
  })

  it('skips publications the user does not subscribe to', () => {
    const microphone = createPublication(Track.Source.Microphone, false)
    const { participant } = createParticipant({ publications: [microphone] })
    applyParticipantPlayback(participant, { volume: 1, isMuted: true })
    expect(microphone.setEnabled).not.toHaveBeenCalled()
  })

  it('falls back to 100 % when the browser cannot amplify', () => {
    const { participant, volumes } = createParticipant({ maxElementVolume: 1 })
    applyParticipantPlayback(participant, { volume: 2, isMuted: false })
    expect(volumes.get(Track.Source.Microphone)).toBe(1)
    expect(volumes.get(Track.Source.ScreenShareAudio)).toBe(1)
  })
})
