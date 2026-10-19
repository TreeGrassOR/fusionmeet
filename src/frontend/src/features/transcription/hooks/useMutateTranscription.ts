import { captureEvent } from '@/features/analytics/telemetry'
import { useStartTranscription } from '../api/startTranscription'
import { useStopTranscription } from '../api/stopTranscription'
import { transcriptionStore } from '../store'

// A transcription already runs (start) or was already stopped (stop): the
// room metadata, hence the UI, catches up on its own.
const START_CONFLICT = 409
const STOP_NOT_FOUND = 404

export const useMutateTranscription = () => {
  const { mutateAsync: startTranscription, isPending: isPendingToStart } =
    useStartTranscription({
      onError: (error) => {
        if (error?.statusCode === START_CONFLICT) return
        transcriptionStore.errorDialog = 'start'
        captureEvent('error-starting-transcription')
      },
    })
  const { mutateAsync: stopTranscription, isPending: isPendingToStop } =
    useStopTranscription({
      onError: (error) => {
        if (error?.statusCode === STOP_NOT_FOUND) return
        transcriptionStore.errorDialog = 'stop'
        captureEvent('error-stopping-transcription')
      },
    })

  return {
    startTranscription,
    isPendingToStart,
    stopTranscription,
    isPendingToStop,
  }
}

export const isExpectedTranscriptionError = (error: unknown) => {
  const statusCode = (error as { statusCode?: number } | undefined)?.statusCode
  return statusCode === START_CONFLICT || statusCode === STOP_NOT_FOUND
}
