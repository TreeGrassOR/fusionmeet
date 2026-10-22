import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from 'vitest'
import {
  Track,
  type LocalParticipant,
  type ScreenShareCaptureOptions,
} from 'livekit-client'

const CHROME_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'

type FakeTrack = {
  source: Track.Source
  mediaStreamTrack: { getSettings: () => Record<string, unknown> }
  stop: Mock<() => void>
}

const createTrack = (
  source: Track.Source,
  settings: Record<string, unknown> = {}
): FakeTrack => ({
  source,
  mediaStreamTrack: { getSettings: () => settings },
  stop: vi.fn<() => void>(),
})

const screenTracks = ({
  displaySurface = 'monitor',
  restrictOwnAudio,
  withAudio = true,
}: {
  displaySurface?: string
  restrictOwnAudio?: boolean
  withAudio?: boolean
} = {}) => {
  const video = createTrack(Track.Source.ScreenShare, { displaySurface })
  const audio = createTrack(
    Track.Source.ScreenShareAudio,
    restrictOwnAudio === undefined ? {} : { restrictOwnAudio }
  )
  return { video, audio, tracks: withAudio ? [video, audio] : [video] }
}

const createParticipant = (
  capture: () => Promise<FakeTrack[]>,
  publish: (track: FakeTrack) => Promise<unknown> = async () => ({})
) => {
  const participant = {
    isScreenShareEnabled: false,
    createScreenTracks: vi.fn(async (options?: ScreenShareCaptureOptions) => {
      void options
      return capture()
    }),
    publishTrack: vi.fn((track: FakeTrack, options?: unknown) => {
      void options
      return publish(track)
    }),
    unpublishTrack: vi.fn(async (track: FakeTrack) => {
      track.stop()
    }),
  }
  return {
    participant,
    lp: participant as unknown as LocalParticipant,
  }
}

const loadModules = async (supportedConstraints: Record<string, boolean>) => {
  vi.stubGlobal('navigator', {
    userAgent: CHROME_UA,
    mediaDevices: { getSupportedConstraints: () => supportedConstraints },
  })
  vi.resetModules()
  return {
    ...(await import('./startScreenShare')),
    ...(await import('./screenShare')),
  }
}

describe('startScreenShare', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('captures with the shared options and publishes both tracks', async () => {
    const {
      startScreenShare,
      SCREEN_SHARE_PUBLISH_OPTIONS,
      SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS,
    } = await loadModules({ restrictOwnAudio: true })
    const { video, audio, tracks } = screenTracks({ restrictOwnAudio: true })
    const { participant, lp } = createParticipant(async () => tracks)

    await expect(startScreenShare(lp)).resolves.toBeNull()

    const [options] = participant.createScreenTracks.mock.calls[0]
    expect(options).toMatchObject({
      audio: { restrictOwnAudio: true },
      selfBrowserSurface: 'exclude',
      systemAudio: 'include',
      resolution: { width: 1920, height: 1080, frameRate: 15 },
    })
    expect(participant.publishTrack).toHaveBeenCalledTimes(2)
    expect(participant.publishTrack).toHaveBeenCalledWith(
      video,
      SCREEN_SHARE_PUBLISH_OPTIONS
    )
    expect(participant.publishTrack).toHaveBeenCalledWith(
      audio,
      SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS
    )
    expect(audio.stop).not.toHaveBeenCalled()
  })

  it('passes a copy of the cached options, which livekit-client mutates', async () => {
    const { startScreenShare, getScreenShareCaptureOptions } =
      await loadModules({ restrictOwnAudio: true })
    const { participant, lp } = createParticipant(
      async () => screenTracks({ restrictOwnAudio: true }).tracks
    )
    await startScreenShare(lp)
    const [options] = participant.createScreenTracks.mock.calls[0]
    expect(options).not.toBe(getScreenShareCaptureOptions())
    expect(options).toEqual(getScreenShareCaptureOptions())
  })

  it('drops the audio of a screen capture the browser did not restrict', async () => {
    // Chromium on Linux: the constraint is "supported" but not applied
    const { startScreenShare } = await loadModules({ restrictOwnAudio: true })
    const { video, audio, tracks } = screenTracks({ restrictOwnAudio: false })
    const { participant, lp } = createParticipant(async () => tracks)

    await expect(startScreenShare(lp)).resolves.toBe('notApplied')

    expect(audio.stop).toHaveBeenCalledTimes(1)
    expect(participant.publishTrack).toHaveBeenCalledTimes(1)
    expect(participant.publishTrack.mock.calls[0][0]).toBe(video)
  })

  it('drops window audio on browsers without restrictOwnAudio', async () => {
    const { startScreenShare } = await loadModules({})
    const { video, audio, tracks } = screenTracks({ displaySurface: 'window' })
    const { participant, lp } = createParticipant(async () => tracks)

    await expect(startScreenShare(lp)).resolves.toBe('unsupported')

    const [options] = participant.createScreenTracks.mock.calls[0]
    expect(options).toMatchObject({ audio: true, systemAudio: 'exclude' })
    expect(audio.stop).toHaveBeenCalled()
    expect(participant.publishTrack.mock.calls.map(([track]) => track)).toEqual(
      [video]
    )
  })

  it('keeps tab audio, which only holds that tab', async () => {
    const { startScreenShare } = await loadModules({})
    const { audio, tracks } = screenTracks({ displaySurface: 'browser' })
    const { participant, lp } = createParticipant(async () => tracks)

    await expect(startScreenShare(lp)).resolves.toBeNull()
    expect(audio.stop).not.toHaveBeenCalled()
    expect(participant.publishTrack).toHaveBeenCalledTimes(2)
  })

  it('publishes the video alone when no audio was captured', async () => {
    const { startScreenShare } = await loadModules({ restrictOwnAudio: true })
    const { video, tracks } = screenTracks({ withAudio: false })
    const { participant, lp } = createParticipant(async () => tracks)

    await expect(startScreenShare(lp)).resolves.toBeNull()
    expect(participant.publishTrack.mock.calls.map(([track]) => track)).toEqual(
      [video]
    )
  })

  it('opens a single picker for concurrent calls', async () => {
    const { startScreenShare } = await loadModules({ restrictOwnAudio: true })
    let resolveCapture: (tracks: FakeTrack[]) => void = () => {}
    const { participant, lp } = createParticipant(
      () => new Promise((resolve) => (resolveCapture = resolve))
    )

    const first = startScreenShare(lp)
    await expect(startScreenShare(lp)).resolves.toBeNull()
    expect(participant.createScreenTracks).toHaveBeenCalledTimes(1)

    resolveCapture(screenTracks({ restrictOwnAudio: true }).tracks)
    await first
    expect(participant.publishTrack).toHaveBeenCalledTimes(2)
  })

  it('does nothing while already sharing', async () => {
    const { startScreenShare } = await loadModules({ restrictOwnAudio: true })
    const { participant, lp } = createParticipant(async () => [])
    participant.isScreenShareEnabled = true

    await expect(startScreenShare(lp)).resolves.toBeNull()
    expect(participant.createScreenTracks).not.toHaveBeenCalled()
  })

  it('rethrows capture errors and allows a new attempt', async () => {
    const { startScreenShare } = await loadModules({ restrictOwnAudio: true })
    const denied = Object.assign(new Error('Permission denied by user'), {
      name: 'NotAllowedError',
    })
    const capture = vi
      .fn<() => Promise<FakeTrack[]>>()
      .mockRejectedValueOnce(denied)
      .mockResolvedValueOnce(screenTracks({ restrictOwnAudio: true }).tracks)
    const { participant, lp } = createParticipant(capture)

    await expect(startScreenShare(lp)).rejects.toBe(denied)
    expect(participant.publishTrack).not.toHaveBeenCalled()

    await expect(startScreenShare(lp)).resolves.toBeNull()
    expect(participant.publishTrack).toHaveBeenCalledTimes(2)
  })

  it('does not leave half a screen share published when publishing fails', async () => {
    const { startScreenShare } = await loadModules({ restrictOwnAudio: true })
    const { video, audio, tracks } = screenTracks({ restrictOwnAudio: true })
    const failure = new Error('publishing failed')
    const { participant, lp } = createParticipant(
      async () => tracks,
      async (track) => {
        if (track === video) throw failure
        return {}
      }
    )

    await expect(startScreenShare(lp)).rejects.toBe(failure)
    expect(participant.unpublishTrack).toHaveBeenCalledWith(audio)
    expect(participant.unpublishTrack).not.toHaveBeenCalledWith(video)
    expect(video.stop).toHaveBeenCalled()
    expect(audio.stop).toHaveBeenCalled()
  })
})
