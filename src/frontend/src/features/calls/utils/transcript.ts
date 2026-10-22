import type { TranscriptDocument } from '../api/types'
import { fileNameFromTitle } from './fileName'

export type TranscriptLine = {
  key: string
  // from the start of the transcript
  offsetMs: number
  identity: string
  name: string
  text: string
}

const pad = (value: number) => String(value).padStart(2, '0')

/**
 * Offset of a phrase as in transcript.md: "[00:12:05]" without brackets.
 */
export const formatOffset = (milliseconds: number) => {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds % 60)}`
}

/**
 * The phrases of a transcript in order, with their offset from its start.
 */
export const getTranscriptLines = (
  document: TranscriptDocument
): TranscriptLine[] => {
  const start = new Date(document.started_at).getTime()
  return document.segments
    .map((segment, index) => ({
      key: `${index}`,
      offsetMs: new Date(segment.start).getTime() - start,
      identity: segment.identity,
      name: segment.name || segment.identity,
      text: segment.text,
    }))
    .sort((a, b) => a.offsetMs - b.offsetMs)
}

export const formatTranscriptLine = (line: TranscriptLine) =>
  `[${formatOffset(line.offsetMs)}] ${line.name}: ${line.text}`

/**
 * The transcript as plain text, one phrase per line, after an optional
 * header.
 */
export const transcriptToText = (
  lines: readonly TranscriptLine[],
  header: readonly string[] = []
) =>
  [
    ...header,
    ...(header.length ? [''] : []),
    ...lines.map(formatTranscriptLine),
  ]
    .join('\n')
    .concat('\n')

const normalize = (value: string) => value.toLocaleLowerCase()

/**
 * The phrases whose speaker or text contains the query (any case).
 */
export const filterTranscriptLines = (
  lines: readonly TranscriptLine[],
  query: string
) => {
  const needle = normalize(query.trim())
  if (!needle) return [...lines]
  return lines.filter(
    (line) =>
      normalize(line.text).includes(needle) ||
      normalize(line.name).includes(needle)
  )
}

/**
 * Split a text around the occurrences of a query, to highlight them.
 */
export const splitOnMatches = (text: string, query: string) => {
  const needle = normalize(query.trim())
  if (!needle) return [{ text, isMatch: false }]
  const haystack = normalize(text)
  // offsets are shared only when lowercasing keeps the length (it does for
  // Latin and Cyrillic)
  if (haystack.length !== text.length) return [{ text, isMatch: false }]
  const parts: { text: string; isMatch: boolean }[] = []
  let from = 0
  for (
    let index = haystack.indexOf(needle);
    index !== -1;
    index = haystack.indexOf(needle, from)
  ) {
    if (index > from)
      parts.push({ text: text.slice(from, index), isMatch: false })
    parts.push({
      text: text.slice(index, index + needle.length),
      isMatch: true,
    })
    from = index + needle.length
  }
  if (from < text.length) parts.push({ text: text.slice(from), isMatch: false })
  return parts
}

/**
 * A colour index per speaker, in order of their first phrase, so that the
 * first speakers of a transcript get distinct colours.
 */
export const speakerColorIndexes = (
  lines: readonly TranscriptLine[],
  count: number
) => {
  const indexes = new Map<string, number>()
  for (const line of lines) {
    const speaker = line.identity || line.name
    if (!indexes.has(speaker)) indexes.set(speaker, indexes.size % count)
  }
  return indexes
}

/**
 * File name of a downloaded transcript.
 */
export const transcriptFileName = (title: string, date: Date) => {
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  const base = fileNameFromTitle(title)
  return `${base ? `${base} ` : ''}${day}.txt`
}
