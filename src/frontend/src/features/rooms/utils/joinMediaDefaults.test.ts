import { describe, expect, it } from 'vitest'
import { resolveJoinMediaChoices } from './joinMediaDefaults'

const BOTH_ON = { audioEnabled: true, videoEnabled: true }
const BOTH_OFF = { audioEnabled: false, videoEnabled: false }

describe('resolveJoinMediaChoices', () => {
  it('keeps the saved state when no switch is on', () => {
    const prefs = {
      join_with_microphone_off: false,
      join_with_camera_off: false,
    }
    expect(resolveJoinMediaChoices(prefs, BOTH_ON)).toEqual(BOTH_ON)
    expect(resolveJoinMediaChoices(prefs, BOTH_OFF)).toEqual(BOTH_OFF)
    expect(
      resolveJoinMediaChoices(prefs, {
        audioEnabled: false,
        videoEnabled: true,
      })
    ).toEqual({ audioEnabled: false, videoEnabled: true })
  })

  it('turns only the microphone off', () => {
    expect(
      resolveJoinMediaChoices(
        { join_with_microphone_off: true, join_with_camera_off: false },
        BOTH_ON
      )
    ).toEqual({ audioEnabled: false, videoEnabled: true })
  })

  it('turns only the camera off', () => {
    expect(
      resolveJoinMediaChoices(
        { join_with_microphone_off: false, join_with_camera_off: true },
        BOTH_ON
      )
    ).toEqual({ audioEnabled: true, videoEnabled: false })
  })

  it('turns both off', () => {
    expect(
      resolveJoinMediaChoices(
        { join_with_microphone_off: true, join_with_camera_off: true },
        BOTH_ON
      )
    ).toEqual(BOTH_OFF)
  })

  it('never turns a device on', () => {
    expect(
      resolveJoinMediaChoices(
        { join_with_microphone_off: true, join_with_camera_off: true },
        BOTH_OFF
      )
    ).toEqual(BOTH_OFF)
  })

  it('treats preferences saved before the option existed as off', () => {
    expect(resolveJoinMediaChoices({}, BOTH_ON)).toEqual(BOTH_ON)
  })
})
