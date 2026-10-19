export type TranscriptionSettingsRights = {
  isAdminOrOwner: boolean
  // The "transcript" recording mode (summary service and WhisperX) is offered.
  isTranscriptModeEnabled: boolean
  // The per-participant transcription (TRANSCRIPTION_ENABLED) is offered.
  isTranscriptionEnabled: boolean
  // The local participant may start the per-participant transcription.
  canTranscribe: boolean
}

/**
 * What the "Transcription" tab of the settings dialog shows.
 *
 * - The meeting language applies to the "transcript" recording mode only, and
 *   stays for owners and administrators as upstream.
 * - The recognition model applies to the per-participant transcription, for
 *   whoever may start it (same rule as the transcription button).
 */
export const getTranscriptionSettingsVisibility = ({
  isAdminOrOwner,
  isTranscriptModeEnabled,
  isTranscriptionEnabled,
  canTranscribe,
}: TranscriptionSettingsRights) => {
  const showLanguage = isAdminOrOwner && isTranscriptModeEnabled
  const showModel = isTranscriptionEnabled && canTranscribe
  return { showLanguage, showModel, showTab: showLanguage || showModel }
}
