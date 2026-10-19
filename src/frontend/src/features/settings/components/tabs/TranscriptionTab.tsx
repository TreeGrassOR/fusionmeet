import { TabPanel, type TabPanelProps } from '@/primitives/Tabs'
import { Field, H } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { RecordingLanguage, recordingStore } from '@/stores/recording'
import { useSnapshot } from 'valtio'
import { useTranscriptionLanguage } from '@/features/settings'
import { useTranscriptionSettingsVisibility } from '@/features/transcription/hooks/useTranscriptionSettingsVisibility'
import { RecognitionModelField } from '@/features/transcription/components/RecognitionModelField'

export type TranscriptionTabProps = Pick<TabPanelProps, 'id'>

export const TranscriptionTab = ({ id }: TranscriptionTabProps) => {
  const { t } = useTranslation('settings', { keyPrefix: 'transcription' })
  const recordingSnap = useSnapshot(recordingStore)

  const { languageOptions } = useTranscriptionLanguage()
  const { showLanguage, showModel } = useTranscriptionSettingsVisibility()

  return (
    <TabPanel padding={'md'} flex id={id}>
      <H lvl={2}>{t('heading')}</H>
      {showLanguage && (
        <Field
          type="select"
          label={t('language.label')}
          items={languageOptions}
          selectedKey={recordingSnap.language}
          onSelectionChange={(lang) => {
            recordingStore.language = lang as RecordingLanguage
          }}
        />
      )}
      {showModel && <RecognitionModelField />}
    </TabPanel>
  )
}
