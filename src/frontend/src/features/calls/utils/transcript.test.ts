import { describe, expect, it } from 'vitest'
import type { TranscriptDocument } from '../api/types'
import {
  filterTranscriptLines,
  formatOffset,
  formatTranscriptLine,
  getTranscriptLines,
  speakerColorIndexes,
  splitOnMatches,
  transcriptFileName,
  transcriptToText,
} from './transcript'

const DOCUMENT: TranscriptDocument = {
  version: 1,
  transcript_id: 't1',
  language: 'ru',
  room: { id: 'r1', name: 'Планёрка' },
  started_at: '2026-10-19T10:00:00.000Z',
  ended_at: '2026-10-19T11:12:00.000Z',
  participants: [
    { identity: 'u1', name: 'Иван Петров' },
    { identity: 'u2', name: 'Мария Соколова' },
  ],
  segments: [
    {
      identity: 'u2',
      name: 'Мария Соколова',
      start: '2026-10-19T10:12:05.900Z',
      end: '2026-10-19T10:12:09.000Z',
      text: 'Давайте начнём с отчёта',
    },
    {
      identity: 'u1',
      name: 'Иван Петров',
      start: '2026-10-19T10:00:05.200Z',
      end: '2026-10-19T10:00:09.000Z',
      text: 'Всем привет',
    },
    {
      identity: 'u3',
      name: '',
      start: '2026-10-19T11:05:00.000Z',
      end: '2026-10-19T11:05:02.000Z',
      text: 'Отчёт готов',
    },
  ],
}

describe('transcript offsets', () => {
  it('formats hours, minutes and seconds like transcript.md', () => {
    expect(formatOffset(0)).toBe('00:00:00')
    expect(formatOffset(5_200)).toBe('00:00:05')
    expect(formatOffset((12 * 60 + 5) * 1000 + 999)).toBe('00:12:05')
    expect(formatOffset((25 * 3600 + 1) * 1000)).toBe('25:00:01')
    expect(formatOffset(-1500)).toBe('00:00:00')
  })

  it('orders the phrases and measures them from the start', () => {
    const lines = getTranscriptLines(DOCUMENT)
    expect(lines.map((line) => formatTranscriptLine(line))).toEqual([
      '[00:00:05] Иван Петров: Всем привет',
      '[00:12:05] Мария Соколова: Давайте начнём с отчёта',
      '[01:05:00] u3: Отчёт готов',
    ])
  })
})

describe('transcript text', () => {
  it('renders a header and one phrase per line', () => {
    const lines = getTranscriptLines(DOCUMENT)
    expect(
      transcriptToText(lines.slice(0, 1), ['Планёрка', '19.10.2026'])
    ).toBe('Планёрка\n19.10.2026\n\n[00:00:05] Иван Петров: Всем привет\n')
    expect(transcriptToText(lines.slice(0, 1))).toBe(
      '[00:00:05] Иван Петров: Всем привет\n'
    )
  })

  it('names the file after the title and the day', () => {
    expect(transcriptFileName('Планёрка: итоги', new Date(2026, 9, 19))).toBe(
      'Планёрка итоги 2026-10-19.txt'
    )
    expect(transcriptFileName('', new Date(2026, 0, 2))).toBe('2026-01-02.txt')
  })
})

describe('transcript search', () => {
  const lines = getTranscriptLines(DOCUMENT)

  it('keeps the phrases matching the text or the speaker, in any case', () => {
    expect(filterTranscriptLines(lines, 'ОТЧЁТ').map((l) => l.key)).toEqual([
      '0',
      '2',
    ])
    expect(filterTranscriptLines(lines, 'иван').map((l) => l.key)).toEqual([
      '1',
    ])
    expect(filterTranscriptLines(lines, '  ')).toHaveLength(3)
  })

  it('splits a text around the matches', () => {
    expect(splitOnMatches('Отчёт и ещё отчёт', 'отчёт')).toEqual([
      { text: 'Отчёт', isMatch: true },
      { text: ' и ещё ', isMatch: false },
      { text: 'отчёт', isMatch: true },
    ])
    expect(splitOnMatches('Всем привет', '')).toEqual([
      { text: 'Всем привет', isMatch: false },
    ])
    expect(splitOnMatches('Всем привет', 'пока')).toEqual([
      { text: 'Всем привет', isMatch: false },
    ])
  })
})

describe('speaker colours', () => {
  it('follow the order of the first phrases', () => {
    const lines = getTranscriptLines(DOCUMENT)
    const indexes = speakerColorIndexes(lines, 8)
    expect([...indexes.entries()]).toEqual([
      ['u1', 0],
      ['u2', 1],
      ['u3', 2],
    ])
    expect([...speakerColorIndexes(lines, 2).values()]).toEqual([0, 1, 0])
  })
})
