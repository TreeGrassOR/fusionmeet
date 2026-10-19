import { describe, expect, it } from 'vitest'
import { isAudioOutputAvailable } from './audioOutput'

const devices = [
  { kind: 'audioinput', deviceId: 'mic' },
  { kind: 'audiooutput', deviceId: 'default' },
  { kind: 'audiooutput', deviceId: 'headset' },
] as const

describe('isAudioOutputAvailable', () => {
  it('finds a connected output device', () => {
    expect(isAudioOutputAvailable(devices, 'headset')).toBe(true)
  })

  it('rejects a device that is gone or is not an output', () => {
    expect(isAudioOutputAvailable(devices, 'unplugged')).toBe(false)
    expect(isAudioOutputAvailable(devices, 'mic')).toBe(false)
  })

  it('assumes availability when devices are listed without ids', () => {
    expect(
      isAudioOutputAvailable(
        [
          { kind: 'audioinput', deviceId: '' },
          { kind: 'audiooutput', deviceId: '' },
        ],
        'headset'
      )
    ).toBe(true)
    expect(isAudioOutputAvailable([], 'headset')).toBe(true)
  })
})
