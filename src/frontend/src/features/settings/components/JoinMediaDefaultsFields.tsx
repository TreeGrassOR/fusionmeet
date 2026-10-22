import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import { Field, H } from '@/primitives'
import { userPreferencesStore } from '@/stores/userPreferences'
import { useMatchesRoute } from '@/navigation/useMatchesRoute'

// Imported on demand: the store module belongs to the room chunk, which the
// join screen has already loaded, so this fetches nothing new.
const turnOffInJoinPreview = (device: 'microphone' | 'camera') => {
  void import('@/stores/userChoices').then(
    ({ saveAudioInputEnabled, saveVideoInputEnabled }) => {
      if (device === 'microphone') saveAudioInputEnabled(false)
      else saveVideoInputEnabled(false)
    }
  )
}

export type JoinMediaDefaultsFieldsProps = {
  /**
   * Outside of a conference only (the join screen): turning a switch on also
   * turns the device off right away, so the open preview stops it. Never in a
   * conference, where the published tracks do not follow the stored choices.
   */
  syncJoinPreview?: boolean
}

/** "Join with the microphone / camera off" switches (per browser). */
export const JoinMediaDefaultsFields = ({
  syncJoinPreview = false,
}: JoinMediaDefaultsFieldsProps) => {
  const { t, ready } = useTranslation('joinDefaults', { useSuspense: false })
  const userPreferencesSnap = useSnapshot(userPreferencesStore)
  const isJoinScreen = useMatchesRoute('room') && syncJoinPreview

  if (!ready) return null

  return (
    <>
      <H lvl={2}>{t('heading')}</H>
      <Field
        type="switch"
        label={t('microphone.label')}
        description={t('microphone.description')}
        isSelected={userPreferencesSnap.join_with_microphone_off}
        onChange={(value) => {
          userPreferencesStore.join_with_microphone_off = value
          if (value && isJoinScreen) turnOffInJoinPreview('microphone')
        }}
        wrapperProps={{
          noMargin: true,
          fullWidth: true,
        }}
      />
      <Field
        type="switch"
        label={t('camera.label')}
        description={t('camera.description')}
        isSelected={userPreferencesSnap.join_with_camera_off}
        onChange={(value) => {
          userPreferencesStore.join_with_camera_off = value
          if (value && isJoinScreen) turnOffInJoinPreview('camera')
        }}
        wrapperProps={{
          noMargin: true,
          fullWidth: true,
        }}
      />
    </>
  )
}
