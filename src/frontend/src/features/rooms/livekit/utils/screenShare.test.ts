import { afterEach, describe, expect, it, vi } from 'vitest'
import { AudioPresets, ScreenSharePresets } from 'livekit-client'
import {
  buildScreenShareCaptureOptions,
  mayCarryConferenceAudio,
  SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS,
  SCREEN_SHARE_PUBLISH_OPTIONS,
} from './screenShare'

const CHROME_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
const SAFARI_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'

const stubNavigator = ({
  userAgent = CHROME_UA,
  supportedConstraints,
}: {
  userAgent?: string
  supportedConstraints?: () => Record<string, boolean>
}) =>
  vi.stubGlobal('navigator', {
    userAgent,
    mediaDevices: supportedConstraints
      ? { getSupportedConstraints: supportedConstraints }
      : undefined,
  })

const loadModule = async () => {
  vi.resetModules()
  return await import('./screenShare')
}

const track = (settings: Record<string, unknown>) =>
  ({ getSettings: () => settings }) as unknown as MediaStreamTrack

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('buildScreenShareCaptureOptions', () => {
  it('asks the browser to remove its own audio where it can', () => {
    expect(
      buildScreenShareCaptureOptions({
        canRestrictOwnAudio: true,
        isSafari: false,
      })
    ).toEqual({
      audio: { restrictOwnAudio: true },
      selfBrowserSurface: 'exclude',
      systemAudio: 'include',
      resolution: expect.objectContaining({
        width: 1920,
        height: 1080,
        frameRate: 15,
      }),
    })
  })

  it('does not offer system audio without restrictOwnAudio', () => {
    expect(
      buildScreenShareCaptureOptions({
        canRestrictOwnAudio: false,
        isSafari: false,
      })
    ).toMatchObject({
      audio: true,
      selfBrowserSurface: 'exclude',
      systemAudio: 'exclude',
    })
  })

  it('never uses exact or min constraints, which getDisplayMedia rejects', () => {
    const { audio } = buildScreenShareCaptureOptions({
      canRestrictOwnAudio: true,
      isSafari: false,
    })
    expect(JSON.stringify(audio)).not.toMatch(/exact|min/)
  })

  it('captures at 15 fps like the encoding, except on Safari', () => {
    expect(
      buildScreenShareCaptureOptions({
        canRestrictOwnAudio: false,
        isSafari: false,
      }).resolution
    ).toEqual(ScreenSharePresets.h1080fps15.resolution)
    expect(
      buildScreenShareCaptureOptions({
        canRestrictOwnAudio: false,
        isSafari: true,
      })
    ).not.toHaveProperty('resolution')
  })
})

describe('isRestrictOwnAudioSupported', () => {
  it('reads the supported constraints', async () => {
    stubNavigator({ supportedConstraints: () => ({ restrictOwnAudio: true }) })
    expect((await loadModule()).isRestrictOwnAudioSupported()).toBe(true)

    stubNavigator({ supportedConstraints: () => ({ echoCancellation: true }) })
    expect((await loadModule()).isRestrictOwnAudioSupported()).toBe(false)
  })

  it('is false without media devices or when the check throws', async () => {
    stubNavigator({})
    expect((await loadModule()).isRestrictOwnAudioSupported()).toBe(false)

    stubNavigator({
      supportedConstraints: () => {
        throw new Error('nope')
      },
    })
    expect((await loadModule()).isRestrictOwnAudioSupported()).toBe(false)
  })
})

describe('getScreenShareCaptureOptions', () => {
  it('follows the browser and is computed once', async () => {
    stubNavigator({ supportedConstraints: () => ({ restrictOwnAudio: true }) })
    const { getScreenShareCaptureOptions } = await loadModule()
    const options = getScreenShareCaptureOptions()
    expect(options).toMatchObject({
      audio: { restrictOwnAudio: true },
      systemAudio: 'include',
      resolution: { frameRate: 15 },
    })
    expect(getScreenShareCaptureOptions()).toBe(options)
  })

  it('leaves the resolution to the browser on Safari', async () => {
    stubNavigator({
      userAgent: SAFARI_UA,
      supportedConstraints: () => ({}),
    })
    const options = (await loadModule()).getScreenShareCaptureOptions()
    expect(options).toMatchObject({ audio: true, systemAudio: 'exclude' })
    expect(options).not.toHaveProperty('resolution')
  })
})

describe('mayCarryConferenceAudio', () => {
  it('is true for a screen or window capture the browser did not restrict', () => {
    for (const displaySurface of ['monitor', 'window', undefined]) {
      expect(
        mayCarryConferenceAudio(
          track({ displaySurface }),
          track({ restrictOwnAudio: false })
        )
      ).toBe(true)
      // browsers before Chrome 141 do not report the setting at all
      expect(
        mayCarryConferenceAudio(track({ displaySurface }), track({}))
      ).toBe(true)
    }
    expect(mayCarryConferenceAudio(undefined, track({}))).toBe(true)
  })

  it('is false once the browser removed its own audio', () => {
    expect(
      mayCarryConferenceAudio(
        track({ displaySurface: 'monitor' }),
        track({ restrictOwnAudio: true })
      )
    ).toBe(false)
  })

  it('is false for tab audio, which only holds that tab', () => {
    expect(
      mayCarryConferenceAudio(
        track({ displaySurface: 'browser' }),
        track({ restrictOwnAudio: false })
      )
    ).toBe(false)
  })
})

describe('SCREEN_SHARE_PUBLISH_OPTIONS', () => {
  it('publishes VP8 simulcast at 1080p, 15 fps, with stereo audio', () => {
    expect(SCREEN_SHARE_PUBLISH_OPTIONS).toEqual({
      videoCodec: 'vp8',
      simulcast: true,
      screenShareEncoding: expect.objectContaining({
        maxBitrate: 2_500_000,
        maxFramerate: 15,
      }),
      audioPreset: AudioPresets.musicStereo,
    })
    expect(Object.isFrozen(SCREEN_SHARE_PUBLISH_OPTIONS)).toBe(true)
  })

  it('publishes the audio in stereo, without RED and DTX', () => {
    expect(SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS).toEqual({
      ...SCREEN_SHARE_PUBLISH_OPTIONS,
      forceStereo: true,
      dtx: false,
      red: false,
    })
    expect(SCREEN_SHARE_PUBLISH_OPTIONS).not.toHaveProperty('forceStereo')
    expect(Object.isFrozen(SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS)).toBe(true)
  })
})
