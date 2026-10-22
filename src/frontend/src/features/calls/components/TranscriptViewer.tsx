import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button as RACButton, Input, SearchField } from 'react-aria-components'
import { RiCloseLine, RiDownloadLine, RiSearchLine } from '@remixicon/react'
import { css, cx } from '@/styled-system/css'
import { Button, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { TranscriptStatus } from '@/features/transcription/types'
import { useCallTranscript } from '../api/fetchCallTranscript'
import type { CallDetailApi, CallTranscriptApi } from '../api/types'
import { downloadTextFile } from '../utils/download'
import {
  filterTranscriptLines,
  formatOffset,
  getTranscriptLines,
  speakerColorIndexes,
  splitOnMatches,
  transcriptFileName,
  transcriptToText,
} from '../utils/transcript'
import { PanelState } from './PanelState'

// Speaker names, readable on the page in both themes
const SPEAKER_COLORS = [
  css({ color: { base: 'blue.700', _dark: 'blue.300' } }),
  css({ color: { base: 'emerald.700', _dark: 'emerald.300' } }),
  css({ color: { base: 'violet.700', _dark: 'violet.300' } }),
  css({ color: { base: 'orange.700', _dark: 'orange.300' } }),
  css({ color: { base: 'pink.700', _dark: 'pink.300' } }),
  css({ color: { base: 'teal.700', _dark: 'teal.300' } }),
  css({ color: { base: 'amber.800', _dark: 'amber.300' } }),
  css({ color: { base: 'indigo.700', _dark: 'indigo.300' } }),
]

const isPending = (transcript: CallTranscriptApi) =>
  transcript.status === TranscriptStatus.Active ||
  transcript.status === TranscriptStatus.Stopping

const isViewable = (transcript: CallTranscriptApi) =>
  transcript.status === TranscriptStatus.Ready && transcript.can_view

const Highlighted = ({ text, query }: { text: string; query: string }) => (
  <>
    {splitOnMatches(text, query).map((part, index) =>
      part.isMatch ? (
        <mark
          key={index}
          className={css({
            backgroundColor: 'warning.subtle',
            color: 'inherit',
            borderRadius: '3px',
            paddingX: '1px',
          })}
        >
          {part.text}
        </mark>
      ) : (
        part.text
      )
    )}
  </>
)

const TranscriptDocumentView = ({
  callId,
  transcript,
  title,
  headerLines,
}: {
  callId: string
  transcript: CallTranscriptApi
  title: string
  headerLines: string[]
}) => {
  const { t } = useTranslation('calls', { keyPrefix: 'transcript' })
  const documentQuery = useCallTranscript(callId, transcript.id)
  const [search, setSearch] = useState('')

  const lines = useMemo(
    () => (documentQuery.data ? getTranscriptLines(documentQuery.data) : []),
    [documentQuery.data]
  )
  const speakerColors = useMemo(
    () => speakerColorIndexes(lines, SPEAKER_COLORS.length),
    [lines]
  )
  const visibleLines = useMemo(
    () => filterTranscriptLines(lines, search),
    [lines, search]
  )
  const query = search.trim()

  if (documentQuery.isLoading) {
    return (
      <PanelState>
        <Spinner size={28} />
      </PanelState>
    )
  }
  if (documentQuery.isError || !documentQuery.data) {
    return (
      <PanelState>
        <Text variant="sm" margin={false} role="alert">
          {t('error')}
        </Text>
        <Button
          variant="secondary"
          size="sm"
          onPress={() => documentQuery.refetch()}
        >
          {t('retry')}
        </Button>
      </PanelState>
    )
  }
  if (!lines.length) {
    return (
      <PanelState>
        <Text variant="note" margin={false}>
          {t('empty')}
        </Text>
      </PanelState>
    )
  }

  return (
    <>
      <div
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '0.75rem',
          paddingX: { base: '1rem', md: '2rem' },
          paddingTop: '1rem',
          paddingBottom: '0.5rem',
        })}
      >
        <SearchField
          value={search}
          onChange={setSearch}
          aria-label={t('search')}
          className={css({
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            flex: '1 1 14rem',
            maxWidth: '24rem',
            '& input::-webkit-search-cancel-button': { display: 'none' },
            '&[data-empty] button': { display: 'none' },
          })}
        >
          <RiSearchLine
            size={18}
            aria-hidden="true"
            className={css({
              position: 'absolute',
              left: '0.75rem',
              color: 'fg.muted',
              pointerEvents: 'none',
            })}
          />
          <Input
            placeholder={t('search')}
            className={css({
              width: '100%',
              height: '2.25rem',
              paddingLeft: '2.5rem',
              paddingRight: '2.25rem',
              borderRadius: '10px',
              border: '1px solid {colors.border}',
              backgroundColor: 'surface.raised',
              color: 'fg',
              fontSize: '0.875rem',
              _placeholder: { color: 'fg.muted' },
              '&[data-focused]': { borderColor: 'accent' },
            })}
          />
          <RACButton
            aria-label={t('clearSearch')}
            className={css({
              position: 'absolute',
              right: '0.375rem',
              display: 'flex',
              padding: '0.25rem',
              borderRadius: 'full',
              color: 'fg.muted',
              cursor: 'pointer',
              '&[data-hovered]': {
                backgroundColor: 'surface.hover',
                color: 'fg',
              },
            })}
          >
            <RiCloseLine size={18} aria-hidden="true" />
          </RACButton>
        </SearchField>
        {query && (
          <Text variant="smNote" margin={false} aria-live="polite">
            {visibleLines.length
              ? t('matches', { count: visibleLines.length })
              : t('noMatches')}
          </Text>
        )}
        <Button
          variant="secondary"
          size="sm"
          className={css({ marginLeft: 'auto', borderRadius: '8px!' })}
          onPress={() =>
            downloadTextFile(
              transcriptToText(lines, headerLines),
              transcriptFileName(
                title,
                new Date(documentQuery.data.started_at)
              ),
              'text/plain;charset=utf-8'
            )
          }
        >
          <RiDownloadLine size={18} aria-hidden="true" />
          {t('download')}
        </Button>
      </div>
      <ol
        aria-label={t('label')}
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.625rem',
          maxWidth: '52rem',
          paddingX: { base: '1rem', md: '2rem' },
          paddingTop: '0.5rem',
          paddingBottom: '1.5rem',
        })}
      >
        {visibleLines.map((line) => (
          <li
            key={line.key}
            className={css({
              display: 'grid',
              gridTemplateColumns: '5rem minmax(0, 1fr)',
              columnGap: '0.75rem',
              fontSize: '0.9375rem',
              lineHeight: 1.5,
            })}
          >
            <span
              className={css({
                color: 'fg.muted',
                fontVariantNumeric: 'tabular-nums',
                fontSize: '0.8125rem',
                paddingTop: '0.125rem',
              })}
            >
              [{formatOffset(line.offsetMs)}]
            </span>
            <p className={css({ minWidth: 0, overflowWrap: 'anywhere' })}>
              <span
                className={cx(
                  css({ fontWeight: 600 }),
                  SPEAKER_COLORS[
                    speakerColors.get(line.identity || line.name) ?? 0
                  ]
                )}
              >
                <Highlighted text={line.name} query={query} />:
              </span>{' '}
              <Highlighted text={line.text} query={query} />
            </p>
          </li>
        ))}
      </ol>
    </>
  )
}

/**
 * The transcripts of a call; a ready one the user may read is shown inline.
 */
export const TranscriptViewer = ({
  call,
  title,
  headerLines,
}: {
  call: CallDetailApi
  title: string
  headerLines: string[]
}) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'transcript' })
  const transcripts = call.transcripts
  const [selectedId, setSelectedId] = useState<string | undefined>()
  const selected =
    transcripts.find((transcript) => transcript.id === selectedId) ??
    transcripts.find(isViewable) ??
    transcripts[transcripts.length - 1]

  const timeFormat = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { timeStyle: 'short' }),
    [i18n.language]
  )

  if (!selected) {
    return (
      <PanelState>
        <Text variant="note" margin={false} wrap="balance">
          {t('none')}
        </Text>
      </PanelState>
    )
  }

  const range = (transcript: CallTranscriptApi) => {
    const start = timeFormat.format(new Date(transcript.started_at))
    return transcript.ended_at
      ? `${start}–${timeFormat.format(new Date(transcript.ended_at))}`
      : start
  }

  return (
    <div className={css({ display: 'flex', flexDirection: 'column', flex: 1 })}>
      {transcripts.length > 1 && (
        <div
          role="group"
          aria-label={t('choose')}
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            gap: '0.5rem',
            paddingX: { base: '1rem', md: '2rem' },
            paddingTop: '1rem',
          })}
        >
          {transcripts.map((transcript) => (
            <Button
              key={transcript.id}
              size="sm"
              variant={
                transcript.id === selected.id ? 'tertiary' : 'quaternaryText'
              }
              aria-pressed={transcript.id === selected.id}
              onPress={() => setSelectedId(transcript.id)}
              className={css({ borderRadius: 'full!' })}
            >
              {t('item', { time: range(transcript) })}
            </Button>
          ))}
        </div>
      )}
      {isViewable(selected) ? (
        <TranscriptDocumentView
          key={selected.id}
          callId={call.id}
          transcript={selected}
          title={title}
          headerLines={headerLines}
        />
      ) : (
        <PanelState>
          {isPending(selected) && <Spinner size={28} />}
          <Text variant="body" margin={false} bold={isPending(selected)}>
            {isPending(selected)
              ? t('pending')
              : selected.status === TranscriptStatus.Failed
                ? t('failed')
                : t('notViewable')}
          </Text>
          {isPending(selected) && (
            <Text variant="smNote" margin={false} wrap="balance">
              {t('pendingHint')}
            </Text>
          )}
        </PanelState>
      )}
    </div>
  )
}
