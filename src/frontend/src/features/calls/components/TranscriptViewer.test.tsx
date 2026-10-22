import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { TranscriptStatus } from '@/features/transcription/types'
import { TranscriptViewer } from './TranscriptViewer'
import type { CallDetailApi, TranscriptDocument } from '../api/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'ru' },
  }),
}))

const START = '2026-10-21T18:00:00.000Z'

const transcriptDocument: TranscriptDocument = {
  version: 1,
  transcript_id: 't1',
  language: 'ru',
  room: { id: 'r1', name: 'abc-defg-hij' },
  started_at: START,
  ended_at: '2026-10-21T18:30:00.000Z',
  participants: [{ identity: 'u1', name: 'Иван' }],
  segments: [
    {
      identity: 'u1',
      name: 'Иван',
      start: '2026-10-21T18:00:05.000Z',
      end: '2026-10-21T18:00:09.000Z',
      text: 'Сроки поставки',
    },
  ],
}

vi.mock('../api/fetchCallTranscript', () => ({
  useCallTranscript: () => ({
    data: transcriptDocument,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}))

const call = {
  id: 'c1',
  transcripts: [
    {
      id: 't1',
      status: TranscriptStatus.Ready,
      started_at: START,
      ended_at: null,
      can_view: true,
    },
  ],
} as unknown as CallDetailApi

describe('TranscriptViewer', () => {
  it('has its match count live region before anything is typed', () => {
    // screen readers announce the changes of a live region already there,
    // not one inserted with its text
    const markup = renderToStaticMarkup(
      <TranscriptViewer call={call} title="Звонок" headerLines={[]} />
    )
    expect(markup).toContain('Сроки поставки')
    expect(markup).toMatch(/<p[^>]*aria-live="polite"[^>]*><\/p>/)
  })
})
