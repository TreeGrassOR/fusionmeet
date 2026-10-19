import { describe, expect, it } from 'vitest'
import { type AsrModelApi, type AsrModelsApi, AsrModelStatus } from '../types'
import {
  getEffectiveModelId,
  getModelOptionLabel,
  getTranscriptModelLabels,
  getUnselectableModelIds,
  hasLoadingModels,
  isChosenModelUnavailable,
  isSelectableModel,
  toAsrModelSetting,
} from './asrModels'

const model = (
  id: string,
  overrides: Partial<AsrModelApi> = {}
): AsrModelApi => ({
  id,
  label: `Label ${id}`,
  description: `Description ${id}`,
  languages: ['ru'],
  punctuation: true,
  device: 'cpu',
  status: AsrModelStatus.Ready,
  error: null,
  ...overrides,
})

const payload = (models: AsrModelApi[], defaultId = 'gigaam-v3') =>
  ({ default: defaultId, models }) satisfies AsrModelsApi

const MODELS = payload([
  model('gigaam-v3', { label: 'Русский — GigaAM v3', device: 'cuda' }),
  model('parakeet-v3', {
    label: 'Русский и английский — Parakeet v3',
    languages: ['ru', 'en'],
  }),
  model('gigaam-multilingual', {
    status: AsrModelStatus.Loading,
    punctuation: false,
  }),
  model('gigaam-multilingual-punct', {
    status: AsrModelStatus.Failed,
    error: 'boom',
    device: null,
  }),
])

const HINTS = {
  default: 'по умолчанию',
  loading: 'загружается',
  failed: 'недоступна',
}

describe('isSelectableModel', () => {
  it('accepts only the models ready to recognize', () => {
    expect(isSelectableModel({ status: AsrModelStatus.Ready })).toBe(true)
    expect(isSelectableModel({ status: AsrModelStatus.Loading })).toBe(false)
    expect(isSelectableModel({ status: AsrModelStatus.Failed })).toBe(false)
  })
})

describe('hasLoadingModels', () => {
  it('tells whether a model is still loading', () => {
    expect(hasLoadingModels(MODELS)).toBe(true)
    expect(hasLoadingModels(payload([model('gigaam-v3')]))).toBe(false)
    expect(hasLoadingModels(undefined)).toBe(false)
  })
})

describe('getEffectiveModelId', () => {
  it('uses the default model when the room chose none', () => {
    expect(getEffectiveModelId(MODELS, null)).toBe('gigaam-v3')
  })

  it('uses the model chosen in the room', () => {
    expect(getEffectiveModelId(MODELS, 'parakeet-v3')).toBe('parakeet-v3')
  })

  it('keeps a chosen model that is still loading', () => {
    expect(getEffectiveModelId(MODELS, 'gigaam-multilingual')).toBe(
      'gigaam-multilingual'
    )
  })

  it('falls back to the default model like the backend does', () => {
    expect(getEffectiveModelId(MODELS, 'gigaam-multilingual-punct')).toBe(
      'gigaam-v3'
    )
    expect(getEffectiveModelId(MODELS, 'removed-model')).toBe('gigaam-v3')
  })
})

describe('isChosenModelUnavailable', () => {
  it('flags a chosen model the room cannot use', () => {
    expect(isChosenModelUnavailable(MODELS, 'gigaam-multilingual-punct')).toBe(
      true
    )
    expect(isChosenModelUnavailable(MODELS, 'removed-model')).toBe(true)
  })

  it('does not flag the default or a usable model', () => {
    expect(isChosenModelUnavailable(MODELS, null)).toBe(false)
    expect(isChosenModelUnavailable(MODELS, 'parakeet-v3')).toBe(false)
    expect(isChosenModelUnavailable(MODELS, 'gigaam-v3')).toBe(false)
  })
})

describe('toAsrModelSetting', () => {
  it('saves null for the default model, so the room follows it', () => {
    expect(toAsrModelSetting(MODELS, 'gigaam-v3')).toBeNull()
  })

  it('saves the id of any other model', () => {
    expect(toAsrModelSetting(MODELS, 'parakeet-v3')).toBe('parakeet-v3')
  })
})

describe('getModelOptionLabel', () => {
  it('marks the default model', () => {
    expect(getModelOptionLabel(MODELS.models[0], 'gigaam-v3', HINTS)).toBe(
      'Русский — GigaAM v3 (по умолчанию)'
    )
  })

  it('shows the label alone for a ready model', () => {
    expect(getModelOptionLabel(MODELS.models[1], 'gigaam-v3', HINTS)).toBe(
      'Русский и английский — Parakeet v3'
    )
  })

  it('tells why a model cannot be chosen', () => {
    expect(getModelOptionLabel(MODELS.models[2], 'gigaam-v3', HINTS)).toBe(
      'Label gigaam-multilingual (загружается)'
    )
    expect(getModelOptionLabel(MODELS.models[3], 'gigaam-v3', HINTS)).toBe(
      'Label gigaam-multilingual-punct (недоступна)'
    )
  })

  it('combines the default mark and the status', () => {
    expect(
      getModelOptionLabel(
        model('gigaam-v3', { label: 'v3', status: AsrModelStatus.Loading }),
        'gigaam-v3',
        HINTS
      )
    ).toBe('v3 (по умолчанию, загружается)')
  })

  it('falls back to the id when the label is empty', () => {
    expect(
      getModelOptionLabel(model('custom', { label: '' }), 'gigaam-v3', HINTS)
    ).toBe('custom')
  })
})

describe('getUnselectableModelIds', () => {
  it('lists the models loading or failed', () => {
    expect(getUnselectableModelIds(MODELS)).toEqual([
      'gigaam-multilingual',
      'gigaam-multilingual-punct',
    ])
  })
})

describe('getTranscriptModelLabels', () => {
  it('maps the ids to their labels, in order', () => {
    expect(
      getTranscriptModelLabels(['parakeet-v3', 'gigaam-v3'], MODELS)
    ).toEqual(['Русский и английский — Parakeet v3', 'Русский — GigaAM v3'])
  })

  it('shows the id of a model the ASR service does not list', () => {
    expect(getTranscriptModelLabels(['old-model'], MODELS)).toEqual([
      'old-model',
    ])
  })

  it('shows the ids when the list is unavailable', () => {
    expect(getTranscriptModelLabels(['gigaam-v3'], undefined)).toEqual([
      'gigaam-v3',
    ])
  })

  it('drops duplicates and empty ids', () => {
    expect(
      getTranscriptModelLabels(['gigaam-v3', '', 'gigaam-v3'], MODELS)
    ).toEqual(['Русский — GigaAM v3'])
  })

  it('is empty for a transcript without models', () => {
    expect(getTranscriptModelLabels(undefined, MODELS)).toEqual([])
    expect(getTranscriptModelLabels([], MODELS)).toEqual([])
  })
})
