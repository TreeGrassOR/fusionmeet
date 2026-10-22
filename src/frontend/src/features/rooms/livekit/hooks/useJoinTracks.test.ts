import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The real modules load the background processors and the stores.
vi.mock('../components/blur', () => ({ BackgroundProcessorFactory: {} }))
vi.mock('@/stores/userChoices', () => ({
  userChoicesStore: { audioEnabled: true, videoEnabled: true },
  saveAudioInputDeviceId: vi.fn(),
  saveAudioInputEnabled: vi.fn(),
  saveVideoInputDeviceId: vi.fn(),
  saveVideoInputEnabled: vi.fn(),
}))
const media = vi.hoisted(() => ({
  noteDeviceReady: vi.fn(),
  onMediaPermissionError: vi.fn(),
}))
vi.mock('../utils/mediaPermissions', async () => {
  const { MediaDeviceFailure } = await import('livekit-client')
  return {
    ...media,
    getMediaDeviceFailure: (error: Error) =>
      MediaDeviceFailure.getFailure(error),
  }
})
vi.mock('@/stores/permissions', () => ({
  isSystemPermissionError: () => false,
}))

import { warmupPermissions } from './useJoinTracks'

type Ready = { audioReady: boolean; videoReady: boolean }

const stream = () => ({ getTracks: () => [{ stop: vi.fn() }] })

const deviceError = (name: string) =>
  Object.assign(new Error(name), { name }) as Error

const harness = () => {
  let state: Ready = { audioReady: false, videoReady: false }
  const setState = (update: Ready | ((current: Ready) => Ready)) => {
    state = typeof update === 'function' ? update(state) : update
  }
  return { setState, get: () => state }
}

describe('warmupPermissions', () => {
  let getUserMedia: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    getUserMedia = vi.fn(async () => stream())
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('opens no device when both are off, and both are ready', async () => {
    const ready = harness()
    await warmupPermissions({ audio: false, video: false }, ready.setState)
    expect(getUserMedia).not.toHaveBeenCalled()
    expect(ready.get()).toEqual({ audioReady: true, videoReady: true })
  })

  it('requests the microphone only, the camera is ready at once', async () => {
    let resolve: (value: unknown) => void = () => {}
    getUserMedia.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    const ready = harness()
    const done = warmupPermissions(
      { audio: true, video: false },
      ready.setState
    )
    expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({
      audio: true,
      video: false,
    })
    // Turning the camera on later must not wait for anything.
    expect(ready.get()).toEqual({ audioReady: false, videoReady: true })
    resolve(stream())
    await done
    expect(media.noteDeviceReady).toHaveBeenCalledExactlyOnceWith('microphone')
    expect(ready.get()).toEqual({ audioReady: true, videoReady: true })
  })

  it('keeps one combined request when both are needed', async () => {
    const ready = harness()
    await warmupPermissions({ audio: true, video: true }, ready.setState)
    expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({
      audio: true,
      video: true,
    })
    expect(media.noteDeviceReady).toHaveBeenCalledExactlyOnceWith(undefined)
    expect(ready.get()).toEqual({ audioReady: true, videoReady: true })
  })

  it('reports a single-kind failure for that kind without a retry', async () => {
    const error = deviceError('NotFoundError')
    getUserMedia.mockRejectedValueOnce(error)
    const ready = harness()
    await warmupPermissions({ audio: false, video: true }, ready.setState)
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(media.onMediaPermissionError).toHaveBeenCalledExactlyOnceWith(
      error,
      'camera'
    )
    expect(ready.get()).toEqual({ audioReady: true, videoReady: true })
  })

  it('still retries per kind when a combined request fails', async () => {
    getUserMedia.mockRejectedValueOnce(deviceError('NotFoundError'))
    const ready = harness()
    await warmupPermissions({ audio: true, video: true }, ready.setState)
    await vi.waitFor(() =>
      expect(ready.get()).toEqual({ audioReady: true, videoReady: true })
    )
    expect(getUserMedia.mock.calls).toEqual([
      [{ audio: true, video: true }],
      [{ audio: true }],
      [{ video: true }],
    ])
  })
})
