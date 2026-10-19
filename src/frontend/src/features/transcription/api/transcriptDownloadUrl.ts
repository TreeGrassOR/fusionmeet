import { apiUrl } from '@/api/apiUrl'
import type { TranscriptFormat } from '../types'

/**
 * Absolute URL of a transcript file. The backend serves it as an
 * attachment to the users of the transcript (session cookie).
 */
export const transcriptDownloadUrl = (
  transcriptId: string,
  format: TranscriptFormat
) =>
  apiUrl(
    `transcripts/${transcriptId}/download/?format=${encodeURIComponent(format)}`
  )
