import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { useTitle } from 'hoofd'
import { css } from '@/styled-system/css'
import { HStack, VStack } from '@/styled-system/jsx'
import { mediaUrl } from '@/api/mediaUrl'
import { useUser } from '@/features/auth/api/useUser'
import { LoginPrompt } from '@/features/recording/components/LoginPrompt'
import type { RecordingApi } from '@/features/recording/api/fetchRecording'
import { LoadingScreen } from '@/components/LoadingScreen'
import { Screen } from '@/layout/Screen'
import { H, Icon, LinkButton, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { formatDate } from '@/utils/formatDate'
import { useAsrModels } from '../api/listAsrModels'
import { listRecordings } from '../api/listRecordings'
import { listTranscripts } from '../api/listTranscripts'
import { transcriptionKeys } from '../api/queryKeys'
import { transcriptDownloadUrl } from '../api/transcriptDownloadUrl'
import { useIsTranscriptionEnabled } from '../hooks/useIsTranscriptionEnabled'
import type { TranscriptApi } from '../types'
import { getTranscriptModelLabels } from '../utils/asrModels'
import {
  Availability,
  getRecordingAvailability,
  getRecordingStart,
  getTranscriptAvailability,
  getTranscriptFormats,
  getTranscriptStart,
  groupIntoMeetings,
  hasPendingItems,
  isVideoRecording,
  type Meeting,
} from '../utils/meetings'

const APP_TITLE = import.meta.env.VITE_APP_TITLE ?? ''

const POLLING_INTERVAL_MS = 10_000

const useDateFormatters = () => {
  const { i18n } = useTranslation()
  return useMemo(() => {
    const locale = i18n.language
    return {
      dateTime: new Intl.DateTimeFormat(locale, {
        dateStyle: 'long',
        timeStyle: 'short',
      }),
      time: new Intl.DateTimeFormat(locale, { timeStyle: 'short' }),
      date: new Intl.DateTimeFormat(locale, { dateStyle: 'long' }),
    }
  }, [i18n.language])
}

const rowClassName = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: '0.5rem 1rem',
  paddingY: '0.75rem',
  borderTop: '1px solid',
  borderColor: 'greyscale.100',
})

const StatusText = ({
  availability,
  children,
}: {
  availability: Availability
  children: React.ReactNode
}) => {
  const isPending =
    availability === Availability.InProgress ||
    availability === Availability.Processing
  return (
    <HStack gap={0.5}>
      {isPending && <Spinner size={18} />}
      <Text
        variant={availability === Availability.Failed ? 'warning' : 'smNote'}
        margin={false}
      >
        {children}
      </Text>
    </HStack>
  )
}

// Static keys, so the i18n extractor keeps them.
const recordingStatusLabel = (availability: Availability, t: TFunction) => {
  switch (availability) {
    case Availability.InProgress:
      return t('recording.status.inProgress')
    case Availability.Processing:
      return t('recording.status.processing')
    case Availability.Expired:
      return t('recording.status.expired')
    default:
      return t('recording.status.failed')
  }
}

const transcriptStatusLabel = (availability: Availability, t: TFunction) => {
  switch (availability) {
    case Availability.InProgress:
      return t('transcript.status.inProgress')
    case Availability.Processing:
      return t('transcript.status.processing')
    default:
      return t('transcript.status.failed')
  }
}

const RecordingRow = ({ recording }: { recording: RecordingApi }) => {
  const { t } = useTranslation('transcription', {
    keyPrefix: 'myRecordings',
  })
  const formatters = useDateFormatters()
  const availability = getRecordingAvailability(recording)
  const isVideo = isVideoRecording(recording)

  return (
    <li className={rowClassName}>
      <HStack gap={0.5}>
        <Icon name="screen_record" />
        <VStack gap={0} alignItems="start">
          <Text variant="body" margin={false}>
            {isVideo ? t('recording.video') : t('recording.audio')}
          </Text>
          <Text variant="smNote" margin={false}>
            {t('recording.startedAt', {
              time: formatters.time.format(getRecordingStart(recording)),
            })}
          </Text>
        </VStack>
      </HStack>
      {availability === Availability.Available ? (
        <VStack gap={0.25} alignItems="end">
          <LinkButton
            href={mediaUrl(recording.key)}
            download={`${recording.room.name}-${formatDate(recording.created_at)}`}
            variant="secondary"
            size="sm"
            aria-label={
              isVideo
                ? t('recording.downloadAriaLabel.video')
                : t('recording.downloadAriaLabel.audio')
            }
          >
            {t('recording.download')}
          </LinkButton>
          {!!recording.expired_at && (
            <Text variant="xsNote" margin={false}>
              {t('recording.expiresOn', {
                date: formatters.date.format(new Date(recording.expired_at)),
              })}
            </Text>
          )}
        </VStack>
      ) : (
        <StatusText availability={availability}>
          {availability === Availability.Expired && recording.expired_at
            ? t('recording.expiredOn', {
                date: formatters.date.format(new Date(recording.expired_at)),
              })
            : recordingStatusLabel(availability, t)}
        </StatusText>
      )}
    </li>
  )
}

const TranscriptRow = ({ transcript }: { transcript: TranscriptApi }) => {
  const { t } = useTranslation('transcription', {
    keyPrefix: 'myRecordings',
  })
  const formatters = useDateFormatters()
  const availability = getTranscriptAvailability(transcript)
  const formats = getTranscriptFormats(transcript)

  // Labels of the models come from the ASR service; their ids are shown
  // when it cannot be reached.
  const isTranscriptionEnabled = useIsTranscriptionEnabled()
  const asrModelsQuery = useAsrModels({
    enabled: isTranscriptionEnabled && !!transcript.models?.length,
  })
  const modelLabels = asrModelsQuery.isLoading
    ? []
    : getTranscriptModelLabels(transcript.models, asrModelsQuery.data)

  const start = formatters.time.format(getTranscriptStart(transcript))
  const time = transcript.ended_at
    ? t('transcript.timeRange', {
        start,
        end: formatters.time.format(new Date(transcript.ended_at)),
      })
    : t('transcript.startedAt', { time: start })

  return (
    <li className={rowClassName}>
      <HStack gap={0.5}>
        <Icon name="speech_to_text" />
        <VStack gap={0} alignItems="start">
          <Text variant="body" margin={false}>
            {t('transcript.label')}
          </Text>
          <Text variant="smNote" margin={false}>
            {time}
          </Text>
          {modelLabels.length > 0 && (
            <Text variant="smNote" margin={false}>
              {t('transcript.models', {
                count: modelLabels.length,
                names: modelLabels.join(', '),
              })}
            </Text>
          )}
        </VStack>
      </HStack>
      {availability === Availability.Available ? (
        <HStack gap={0.5}>
          {formats.map((format) => (
            <LinkButton
              key={format}
              href={transcriptDownloadUrl(transcript.id, format)}
              variant="secondary"
              size="sm"
              aria-label={
                format === 'md'
                  ? t('transcript.downloadAriaLabel.md')
                  : t('transcript.downloadAriaLabel.json')
              }
            >
              {format === 'md'
                ? t('transcript.download.md')
                : t('transcript.download.json')}
            </LinkButton>
          ))}
        </HStack>
      ) : (
        <StatusText availability={availability}>
          {transcriptStatusLabel(availability, t)}
        </StatusText>
      )}
    </li>
  )
}

const MeetingCard = ({ meeting }: { meeting: Meeting }) => {
  const { t } = useTranslation('transcription', { keyPrefix: 'myRecordings' })
  const formatters = useDateFormatters()
  const headingId = `meeting-${meeting.key.replace(/[^a-zA-Z0-9-]/g, '-')}`

  return (
    <section
      aria-labelledby={headingId}
      className={css({
        border: '1px solid',
        borderColor: 'greyscale.200',
        borderRadius: '8px',
        paddingX: '1rem',
        paddingTop: '1rem',
        paddingBottom: '0.25rem',
      })}
    >
      <H lvl={2} margin={false} id={headingId}>
        {meeting.room.name || meeting.room.slug}
      </H>
      <Text variant="smNote" margin={false}>
        {formatters.dateTime.format(meeting.startedAt)}
      </Text>
      {meeting.participants.length > 0 && (
        <Text
          variant="sm"
          margin={false}
          className={css({ marginTop: '0.25rem' })}
        >
          {t('participants', { names: meeting.participants.join(', ') })}
        </Text>
      )}
      <ul className={css({ marginTop: '0.75rem' })}>
        {meeting.recordings.map((recording) => (
          <RecordingRow key={recording.id} recording={recording} />
        ))}
        {meeting.transcripts.map((transcript) => (
          <TranscriptRow key={transcript.id} transcript={transcript} />
        ))}
      </ul>
    </section>
  )
}

const Content = ({ children }: { children: React.ReactNode }) => (
  <div
    className={css({
      width: '38rem',
      maxWidth: '100%',
      marginX: 'auto',
      paddingX: '2rem',
      paddingY: '2rem',
    })}
  >
    {children}
  </div>
)

const MyRecordings = () => {
  const { t } = useTranslation('transcription', { keyPrefix: 'myRecordings' })
  const { isLoggedIn, isLoading: isAuthLoading } = useUser()

  useTitle(`${APP_TITLE} - ${t('title')}`)

  const isEnabled = isLoggedIn === true

  // While a recording or a transcript is being produced, poll its list.
  const recordingsQuery = useQuery({
    queryKey: [transcriptionKeys.myRecordings],
    queryFn: listRecordings,
    enabled: isEnabled,
    retry: false,
    refetchInterval: (query) =>
      hasPendingItems(query.state.data ?? [], []) ? POLLING_INTERVAL_MS : false,
  })
  const transcriptsQuery = useQuery({
    queryKey: [transcriptionKeys.transcripts],
    queryFn: listTranscripts,
    enabled: isEnabled,
    retry: false,
    refetchInterval: (query) =>
      hasPendingItems([], query.state.data ?? []) ? POLLING_INTERVAL_MS : false,
  })

  const recordings = recordingsQuery.data
  const transcripts = transcriptsQuery.data

  const meetings = useMemo(
    () => groupIntoMeetings(recordings ?? [], transcripts ?? []),
    [recordings, transcripts]
  )

  if (isLoggedIn === undefined || isAuthLoading) {
    return <LoadingScreen />
  }

  if (isLoggedIn === false) {
    return (
      <Screen layout="centered" footer={false}>
        <LoginPrompt heading={t('login.heading')} body={t('login.body')} />
      </Screen>
    )
  }

  const isLoading = recordingsQuery.isLoading || transcriptsQuery.isLoading
  const hasRecordingsError = recordingsQuery.isError
  const hasTranscriptsError = transcriptsQuery.isError

  return (
    <Screen layout="fullpage" headerTitle={t('title')}>
      <Content>
        <Text variant="body" margin="md">
          {t('description')}
        </Text>
        {(hasRecordingsError || hasTranscriptsError) && (
          <Text variant="warning" margin="md" role="alert">
            {hasRecordingsError && hasTranscriptsError
              ? t('error.all')
              : hasRecordingsError
                ? t('error.recordings')
                : t('error.transcripts')}
          </Text>
        )}
        {isLoading ? (
          <HStack justify="center" paddingY="2rem">
            <Spinner size={30} />
            <Text variant="body" margin={false}>
              {t('loading')}
            </Text>
          </HStack>
        ) : meetings.length === 0 ? (
          !(hasRecordingsError && hasTranscriptsError) && (
            <Text variant="smNote" margin={false}>
              {t('empty')}
            </Text>
          )
        ) : (
          <VStack gap={1} alignItems="stretch">
            {meetings.map((meeting) => (
              <MeetingCard key={meeting.key} meeting={meeting} />
            ))}
          </VStack>
        )}
      </Content>
    </Screen>
  )
}

export default MyRecordings
