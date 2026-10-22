export type JoinMediaPreferences = {
  join_with_microphone_off: boolean
  join_with_camera_off: boolean
}

export type JoinMediaChoices = {
  audioEnabled: boolean
  videoEnabled: boolean
}

/**
 * Microphone and camera state for a new room entry: a device the user asked
 * to join with turned off starts off, otherwise the last saved state is kept.
 * Missing preferences (stored before the option existed) count as off.
 */
export const resolveJoinMediaChoices = (
  preferences: Partial<JoinMediaPreferences>,
  choices: JoinMediaChoices
): JoinMediaChoices => ({
  audioEnabled: preferences.join_with_microphone_off
    ? false
    : choices.audioEnabled,
  videoEnabled: preferences.join_with_camera_off ? false : choices.videoEnabled,
})
