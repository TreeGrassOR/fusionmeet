import { type AsrModelApi, type AsrModelsApi, AsrModelStatus } from '../types'

export type ModelOptionHints = {
  default: string
  loading: string
  failed: string
}

const modelLabel = (model: Pick<AsrModelApi, 'id' | 'label'>) =>
  model.label || model.id

/**
 * Whether a room can switch to this model now. A model still loading or one
 * that failed to load recognizes nothing.
 */
export const isSelectableModel = (model: Pick<AsrModelApi, 'status'>) =>
  model.status === AsrModelStatus.Ready

export const hasLoadingModels = (payload?: AsrModelsApi) =>
  !!payload?.models?.some((model) => model.status === AsrModelStatus.Loading)

/**
 * Model that recognizes the phrases of a room: its choice while the ASR
 * service offers it and it did not fail to load, the default model otherwise.
 * The backend makes the same fallback for the transcription agent.
 */
export const getEffectiveModelId = (
  payload: AsrModelsApi,
  asrModel: string | null
): string => {
  if (asrModel) {
    const chosen = payload.models.find((model) => model.id === asrModel)
    if (chosen && chosen.status !== AsrModelStatus.Failed) return chosen.id
  }
  return payload.default
}

/**
 * Whether the room chose a model it cannot use anymore: removed from the ASR
 * service, or failed to load.
 */
export const isChosenModelUnavailable = (
  payload: AsrModelsApi,
  asrModel: string | null
) => !!asrModel && getEffectiveModelId(payload, asrModel) !== asrModel

/**
 * Value to save when a model is picked: null for the default model, so the
 * room keeps following the default model of the ASR service.
 */
export const toAsrModelSetting = (
  payload: Pick<AsrModelsApi, 'default'>,
  modelId: string
): string | null => (modelId === payload.default ? null : modelId)

/**
 * Label of a model in the select: its name, then whether it is the default
 * model and why it cannot be chosen.
 */
export const getModelOptionLabel = (
  model: Pick<AsrModelApi, 'id' | 'label' | 'status'>,
  defaultId: string,
  hints: ModelOptionHints
) => {
  const notes = [
    ...(model.id === defaultId ? [hints.default] : []),
    ...(model.status === AsrModelStatus.Loading ? [hints.loading] : []),
    ...(model.status === AsrModelStatus.Failed ? [hints.failed] : []),
  ]
  const label = modelLabel(model)
  return notes.length > 0 ? `${label} (${notes.join(', ')})` : label
}

export const getUnselectableModelIds = (payload: AsrModelsApi) =>
  payload.models
    .filter((model) => !isSelectableModel(model))
    .map((model) => model.id)

/**
 * Labels of the models that recognized a transcript, in order of first use.
 * A model the ASR service does not list (anymore) shows its id.
 */
export const getTranscriptModelLabels = (
  modelIds: string[] | undefined,
  payload?: AsrModelsApi
) => {
  const ids = [...new Set((modelIds ?? []).filter((id) => !!id))]
  return ids.map((id) => {
    const model = payload?.models?.find((m) => m.id === id)
    return model ? modelLabel(model) : id
  })
}
