import { afterEach, describe, expect, it, vi } from 'vitest'
import { isVolumeBoostSupported } from './volumeBoost'

describe('isVolumeBoostSupported', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requires AudioContext.setSinkId', () => {
    class AudioContextWithSinkId {
      setSinkId() {}
    }
    vi.stubGlobal('AudioContext', AudioContextWithSinkId)
    expect(isVolumeBoostSupported()).toBe(true)

    class AudioContextWithoutSinkId {}
    vi.stubGlobal('AudioContext', AudioContextWithoutSinkId)
    expect(isVolumeBoostSupported()).toBe(false)
  })

  it('is disabled without Web Audio', () => {
    vi.stubGlobal('AudioContext', undefined)
    expect(isVolumeBoostSupported()).toBe(false)
  })
})
