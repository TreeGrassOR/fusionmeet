import { userPreferencesStore } from '@/stores/userPreferences'
import {
  saveAudioInputEnabled,
  saveVideoInputEnabled,
  userChoicesStore,
} from '@/stores/userChoices'
import { resolveJoinMediaChoices } from './joinMediaDefaults'

/**
 * Turns the microphone and/or the camera off for this room entry when the
 * user asked to join with them off. Called once per entry, before the join
 * screen or the conference first reads `userChoicesStore`. The user can still
 * turn them on afterwards.
 */
export const applyJoinMediaDefaults = () => {
  const { audioEnabled, videoEnabled } = resolveJoinMediaChoices(
    userPreferencesStore,
    userChoicesStore
  )
  if (audioEnabled !== userChoicesStore.audioEnabled) {
    saveAudioInputEnabled(audioEnabled)
  }
  if (videoEnabled !== userChoicesStore.videoEnabled) {
    saveVideoInputEnabled(videoEnabled)
  }
}
