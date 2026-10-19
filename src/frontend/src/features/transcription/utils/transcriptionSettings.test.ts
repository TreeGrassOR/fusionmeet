import { describe, expect, it } from 'vitest'
import {
  getTranscriptionSettingsVisibility,
  type TranscriptionSettingsRights,
} from './transcriptionSettings'

const rights = (
  overrides: Partial<TranscriptionSettingsRights> = {}
): TranscriptionSettingsRights => ({
  isAdminOrOwner: false,
  isTranscriptModeEnabled: false,
  isTranscriptionEnabled: false,
  canTranscribe: false,
  ...overrides,
})

describe('getTranscriptionSettingsVisibility', () => {
  it('hides the tab when neither transcription is offered', () => {
    expect(
      getTranscriptionSettingsVisibility(
        rights({ isAdminOrOwner: true, canTranscribe: true })
      )
    ).toEqual({ showLanguage: false, showModel: false, showTab: false })
  })

  it('shows the model to whoever may start the transcription', () => {
    expect(
      getTranscriptionSettingsVisibility(
        rights({ isTranscriptionEnabled: true, canTranscribe: true })
      )
    ).toEqual({ showLanguage: false, showModel: true, showTab: true })
  })

  it('hides the model from participants who may not transcribe', () => {
    expect(
      getTranscriptionSettingsVisibility(
        rights({ isTranscriptionEnabled: true, canTranscribe: false })
      )
    ).toEqual({ showLanguage: false, showModel: false, showTab: false })
  })

  it('shows the meeting language to owners when the transcript mode is offered', () => {
    expect(
      getTranscriptionSettingsVisibility(
        rights({ isAdminOrOwner: true, isTranscriptModeEnabled: true })
      )
    ).toEqual({ showLanguage: true, showModel: false, showTab: true })
  })

  it('keeps the meeting language for owners and administrators only', () => {
    expect(
      getTranscriptionSettingsVisibility(
        rights({ isTranscriptModeEnabled: true, canTranscribe: true })
      )
    ).toEqual({ showLanguage: false, showModel: false, showTab: false })
  })

  it('shows both when both transcriptions are offered', () => {
    expect(
      getTranscriptionSettingsVisibility(
        rights({
          isAdminOrOwner: true,
          isTranscriptModeEnabled: true,
          isTranscriptionEnabled: true,
          canTranscribe: true,
        })
      )
    ).toEqual({ showLanguage: true, showModel: true, showTab: true })
  })
})
