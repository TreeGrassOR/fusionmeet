import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Field, Text } from '@/primitives'
import { css } from '@/styled-system/css'
import { useRoomId } from '@/features/rooms/livekit/hooks/useRoomId'
import { useAsrModels } from '../api/listAsrModels'
import {
  useRoomTranscriptionSettings,
  useUpdateRoomTranscriptionSettings,
} from '../api/roomTranscriptionSettings'
import {
  getEffectiveModelId,
  getModelOptionLabel,
  getUnselectableModelIds,
  isChosenModelUnavailable,
  toAsrModelSetting,
} from '../utils/asrModels'

// Without margin={false} on the notes: its `margin: 0 !important` would
// cancel this top margin.
const noteClassName = css({ marginTop: '0.5rem' })

/**
 * Recognition model of the room transcription, saved as soon as it changes.
 * The agent reads it again within seconds: the next phrases use it.
 * Must be used inside a LiveKit room, by a participant who may transcribe.
 */
export const RecognitionModelField = () => {
  // No suspense: a namespace loading in the middle of the room would blank
  // the whole app (the only Suspense boundary is at its root).
  const { t, ready } = useTranslation('transcription', {
    keyPrefix: 'modelSelect',
    useSuspense: false,
  })
  const roomId = useRoomId()

  const modelsQuery = useAsrModels({ pollWhileLoading: true })
  const settingsQuery = useRoomTranscriptionSettings(roomId)
  const {
    mutate: updateSettings,
    isPending,
    isSuccess,
    isError,
  } = useUpdateRoomTranscriptionSettings()

  const payload = modelsQuery.data
  const asrModel = settingsQuery.data?.asr_model ?? null

  const items = useMemo(
    () =>
      (payload?.models ?? []).map((model) => ({
        value: model.id,
        label: getModelOptionLabel(model, payload?.default ?? '', {
          default: t('default'),
          loading: t('status.loading'),
          failed: t('status.failed'),
        }),
      })),
    [payload, t]
  )

  if (!ready) return null

  if (modelsQuery.isLoading || settingsQuery.isLoading) {
    return (
      <Text variant="smNote" margin="md">
        {t('loading')}
      </Text>
    )
  }

  if (!payload || !settingsQuery.data || !roomId) {
    return (
      <Text variant="warning" margin="md" role="alert">
        {t('loadError')}
      </Text>
    )
  }

  const selectedId = getEffectiveModelId(payload, asrModel)
  const selectedModel = payload.models.find((model) => model.id === selectedId)

  return (
    <div className={css({ marginBottom: 'textfield' })}>
      <Field
        type="select"
        label={t('label')}
        items={items}
        selectedKey={selectedId}
        disabledKeys={getUnselectableModelIds(payload)}
        isDisabled={isPending}
        wrapperProps={{ noMargin: true }}
        onSelectionChange={(key) => {
          if (key === null || String(key) === selectedId) return
          updateSettings({
            id: roomId,
            asrModel: toAsrModelSetting(payload, String(key)),
          })
        }}
      />
      {!!selectedModel?.description && (
        <Text variant="smNote" className={noteClassName}>
          {selectedModel.description}
        </Text>
      )}
      {isChosenModelUnavailable(payload, asrModel) && (
        <Text variant="smNote" className={noteClassName}>
          {t('chosenUnavailable')}
        </Text>
      )}
      {isSuccess && (
        <Text variant="sm" className={noteClassName} role="status">
          {t('saved')}
        </Text>
      )}
      {isError && (
        <Text variant="warning" className={noteClassName} role="alert">
          {t('saveError')}
        </Text>
      )}
    </div>
  )
}
