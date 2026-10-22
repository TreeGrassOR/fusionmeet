import { beforeEach, describe, expect, it, vi } from 'vitest'

// The real store module loads LiveKit and the background processors.
const choices = vi.hoisted(() => ({
  userChoicesStore: { audioEnabled: true, videoEnabled: true },
  saveAudioInputEnabled: vi.fn(),
  saveVideoInputEnabled: vi.fn(),
}))
vi.mock('@/stores/userChoices', () => {
  choices.saveAudioInputEnabled.mockImplementation((enabled: boolean) => {
    choices.userChoicesStore.audioEnabled = enabled
  })
  choices.saveVideoInputEnabled.mockImplementation((enabled: boolean) => {
    choices.userChoicesStore.videoEnabled = enabled
  })
  return choices
})
vi.mock('@/stores/userPreferences', () => ({
  userPreferencesStore: {
    join_with_microphone_off: false,
    join_with_camera_off: false,
  },
}))

import { userPreferencesStore } from '@/stores/userPreferences'
import { applyJoinMediaDefaults } from './applyJoinMediaDefaults'

describe('applyJoinMediaDefaults', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    choices.userChoicesStore.audioEnabled = true
    choices.userChoicesStore.videoEnabled = true
    userPreferencesStore.join_with_microphone_off = false
    userPreferencesStore.join_with_camera_off = false
  })

  it('writes nothing when no switch is on', () => {
    applyJoinMediaDefaults()
    expect(choices.saveAudioInputEnabled).not.toHaveBeenCalled()
    expect(choices.saveVideoInputEnabled).not.toHaveBeenCalled()
  })

  it('turns the requested devices off', () => {
    userPreferencesStore.join_with_camera_off = true
    applyJoinMediaDefaults()
    expect(choices.saveAudioInputEnabled).not.toHaveBeenCalled()
    expect(choices.saveVideoInputEnabled).toHaveBeenCalledWith(false)
    expect(choices.userChoicesStore).toEqual({
      audioEnabled: true,
      videoEnabled: false,
    })
  })

  it('is idempotent (StrictMode calls initializers twice)', () => {
    userPreferencesStore.join_with_microphone_off = true
    userPreferencesStore.join_with_camera_off = true
    applyJoinMediaDefaults()
    applyJoinMediaDefaults()
    expect(choices.saveAudioInputEnabled).toHaveBeenCalledTimes(1)
    expect(choices.saveVideoInputEnabled).toHaveBeenCalledTimes(1)
    expect(choices.userChoicesStore).toEqual({
      audioEnabled: false,
      videoEnabled: false,
    })
  })
})
