import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Tab, TabList, TabPanel, Tabs } from 'react-aria-components'
import { Link } from 'wouter'
import { useTitle } from 'hoofd'
import { RiArrowLeftLine, RiPhoneLine } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { Button, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { ApiError } from '@/api/ApiError'
import { navigateTo } from '@/navigation/navigateTo'
import { useCall } from '../api/fetchCall'
import { useMarkCallRead } from '../api/markCallRead'
import type { CallDetailApi } from '../api/types'
import { useScrollHint } from '../hooks/useScrollHint'
import { callsUiStore } from '../store'
import { isOpenedFromList } from '../utils/backToList'
import { getCallTitle } from '../utils/callTitle'
import { formatDuration } from '../utils/dates'
import { CallAvatar } from './CallAvatar'
import { CallParticipantsList } from './CallParticipantsList'
import { CallRecordingsList } from './CallRecordingsList'
import { ChatHistory } from './ChatHistory'
import { PanelState } from './PanelState'
import { TranscriptViewer } from './TranscriptViewer'

const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Fusion Meet'

// avatars shown in the header, the others are counted
const HEADER_AVATARS = 4
const HEADER_AVATAR_SIZE = 30

const tabClass = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.375rem',
  flexShrink: 0,
  paddingX: { base: '0.5rem', lg: '0.875rem' },
  paddingY: '0.4375rem',
  borderRadius: 'full',
  // the four tabs fit a 360 px phone
  fontSize: { base: '0.8125rem', xs: '0.875rem' },
  fontWeight: 500,
  color: 'fg.muted',
  cursor: 'pointer',
  outline: 'none',
  whiteSpace: 'nowrap',
  transition: 'background 150ms, color 150ms',
  '&[data-hovered]': { backgroundColor: 'surface.hover', color: 'fg' },
  '&[data-selected]': {
    backgroundColor: 'accent.subtle',
    color: 'accent.text',
  },
  '&[data-focus-visible]': {
    outline: '2px solid {colors.focusRing}',
    outlineOffset: '1px',
  },
})

const panelClass = css({
  flex: 1,
  minHeight: 0,
  overflowY: 'auto',
  display: 'flex',
  flexDirection: 'column',
  outline: 'none',
  '&[data-focus-visible]': {
    outline: '2px solid {colors.focusRing}',
    outlineOffset: '-2px',
  },
})

/**
 * Back to the list, on narrow screens. A call opened from the list goes back
 * in the history (the back button of the phone then leaves the page instead
 * of opening the call again); a link to the call is replaced by the list.
 */
const BackLink = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'detail' })
  return (
    <Link
      href="/"
      replace
      onClick={(event) => {
        if (!isOpenedFromList(window.history.state)) return
        event.preventDefault()
        window.history.back()
      }}
      aria-label={t('back')}
      className={css({
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '2.5rem',
        height: '2.5rem',
        borderRadius: 'full',
        color: 'fg',
        flexShrink: 0,
        _hover: { backgroundColor: 'surface.hover' },
        md: { display: 'none' },
      })}
    >
      <RiArrowLeftLine size={22} aria-hidden="true" />
    </Link>
  )
}

const TabLabel = ({ label, count }: { label: string; count?: number }) => (
  <>
    {label}
    {!!count && (
      <span
        className={css({
          fontSize: '0.75rem',
          fontVariantNumeric: 'tabular-nums',
          opacity: 0.8,
        })}
      >
        {count}
      </span>
    )}
  </>
)

const ParticipantAvatars = ({ call }: { call: CallDetailApi }) => {
  const { t } = useTranslation('calls', { keyPrefix: 'detail' })
  const shown = call.participants.slice(0, HEADER_AVATARS)
  const more = call.participants_count - shown.length
  if (!shown.length) return null
  return (
    <div
      role="img"
      aria-label={t('participantsLabel', {
        names: call.participants.map((p) => p.name).join(', '),
      })}
      className={css({ display: 'flex', alignItems: 'center', gap: '0.25rem' })}
    >
      {shown.map((participant, index) => (
        <CallAvatar
          key={`${index}-${participant.name}`}
          name={participant.name || '?'}
          colorId={participant.name}
          size={HEADER_AVATAR_SIZE}
        />
      ))}
      {more > 0 && (
        <span
          aria-hidden="true"
          className={css({
            minWidth: '1.875rem',
            height: '1.875rem',
            paddingX: '0.375rem',
            borderRadius: 'full',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: 'surface.pressed',
            color: 'fg',
            fontSize: '0.75rem',
            fontWeight: 600,
          })}
        >
          +{more}
        </span>
      )}
    </div>
  )
}

const useCallDescription = (call: CallDetailApi | undefined) => {
  const { t, i18n } = useTranslation('calls')
  return useMemo(() => {
    if (!call) return { title: '', when: '', headerLines: [] }
    const locale = i18n.language
    const now = new Date()
    const start = new Date(call.started_at)
    const end = call.ended_at ? new Date(call.ended_at) : null
    const day = new Intl.DateTimeFormat(locale, {
      day: 'numeric',
      month: 'long',
      ...(start.getFullYear() !== now.getFullYear() && { year: 'numeric' }),
    }).format(start)
    const hours = new Intl.DateTimeFormat(locale, { timeStyle: 'short' })
    const when = end
      ? t('detail.when', {
          day,
          start: hours.format(start),
          end: hours.format(end),
          duration: formatDuration(end.getTime() - start.getTime(), locale),
        })
      : t('detail.since', { day, start: hours.format(start) })
    const title = getCallTitle(call, t, locale, now)
    return {
      title,
      when,
      headerLines: [t('transcript.fileHeading', { title }), when],
    }
  }, [call, i18n.language, t])
}

const DetailFrame = ({ children }: { children: ReactNode }) => (
  <div
    className={css({
      display: 'flex',
      flexDirection: 'column',
      flex: 1,
      minHeight: 0,
    })}
  >
    {children}
  </div>
)

/**
 * A call of the history: what it was, then its chat, transcript,
 * recordings and participants in tabs.
 */
export const CallDetail = ({
  callId,
  isListHidden = false,
}: {
  callId: string
  // narrow screens show the call in place of the list
  isListHidden?: boolean
}) => {
  const { t } = useTranslation('calls', { keyPrefix: 'detail' })
  const callQuery = useCall(callId)
  const call = callQuery.data
  const { title, when, headerLines } = useCallDescription(call)
  const { mutate: markRead } = useMarkCallRead()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const tabList = useScrollHint<HTMLDivElement>()

  useTitle(title ? `${title} - ${APP_TITLE}` : APP_TITLE)

  // read when opened, and again when messages arrive while it is open
  const messagesCount = call?.messages_count
  useEffect(() => {
    if (messagesCount !== undefined) markRead(callId)
  }, [callId, messagesCount, markRead])

  // The row that opened the call is hidden with the list on narrow screens:
  // the focus goes to the call, once it is there
  const isLoaded = !!call
  useEffect(() => {
    if (!isLoaded || callsUiStore.openedCallId !== callId) return
    callsUiStore.openedCallId = null
    if (!isListHidden) return
    const active = document.activeElement
    const isFocusLost =
      !active ||
      active === document.body ||
      (active instanceof HTMLElement && !active.offsetParent)
    if (isFocusLost) headingRef.current?.focus()
    // once per call (the component is keyed by the call)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded])

  if (callQuery.isLoading) {
    return (
      <DetailFrame>
        <PanelState>
          <Spinner size={32} />
        </PanelState>
      </DetailFrame>
    )
  }

  if (!call) {
    const isNotFound =
      callQuery.error instanceof ApiError &&
      [403, 404].includes(callQuery.error.statusCode)
    return (
      <DetailFrame>
        <div className={css({ padding: '0.75rem' })}>
          <BackLink />
        </div>
        <PanelState>
          <Text variant="body" margin={false} role="alert">
            {isNotFound ? t('notFound') : t('error')}
          </Text>
          {!isNotFound && (
            <Button
              variant="secondary"
              size="sm"
              onPress={() => callQuery.refetch()}
            >
              {t('retry')}
            </Button>
          )}
        </PanelState>
      </DetailFrame>
    )
  }

  return (
    <DetailFrame>
      <header
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '0.75rem 1.25rem',
          paddingX: { base: '0.75rem', md: '2rem' },
          paddingTop: { base: '0.75rem', md: '1.5rem' },
          paddingBottom: '1rem',
        })}
      >
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            flex: '1 1 16rem',
            minWidth: 0,
          })}
        >
          <BackLink />
          <div className={css({ minWidth: 0 })}>
            <h1
              ref={headingRef}
              tabIndex={-1}
              className={css({
                fontSize: { base: '1.25rem', md: '1.5rem' },
                fontWeight: 700,
                lineHeight: 1.25,
                overflowWrap: 'anywhere',
              })}
            >
              {title}
            </h1>
            <p
              className={css({
                display: 'flex',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: '0.5rem',
                marginTop: '0.25rem',
                fontSize: '0.875rem',
                color: 'fg.muted',
              })}
            >
              {call.is_active && (
                <span
                  className={css({
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '0.375rem',
                    paddingX: '0.5rem',
                    paddingY: '0.0625rem',
                    borderRadius: 'full',
                    backgroundColor: 'success.subtle',
                    color: 'success.subtle-text',
                    fontWeight: 600,
                    fontSize: '0.75rem',
                  })}
                >
                  <span
                    aria-hidden="true"
                    className={css({
                      width: '0.5rem',
                      height: '0.5rem',
                      borderRadius: 'full',
                      backgroundColor: 'success.500',
                    })}
                  />
                  {t('active')}
                </span>
              )}
              <span>{when}</span>
              {!call.participated && <span>{t('notAttended')}</span>}
            </p>
          </div>
        </div>
        <div
          className={css({
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.75rem 1rem',
            minWidth: 0,
            maxWidth: '100%',
            width: { base: '100%', md: 'auto' },
          })}
        >
          <ParticipantAvatars call={call} />
          {call.can_rejoin && (
            <Button
              variant="primary"
              size="sm"
              onPress={() => navigateTo('room', call.room.slug)}
              className={css({
                borderRadius: '10px!',
                paddingX: '0.875rem!',
                paddingY: '0.5rem!',
                whiteSpace: 'nowrap',
              })}
            >
              <RiPhoneLine size={18} aria-hidden="true" />
              {t('rejoin')}
            </Button>
          )}
        </div>
      </header>
      <Tabs
        onSelectionChange={(key) => {
          // a tab cut by the edge of a narrow screen comes into view
          tabList.element
            ?.querySelector(`[data-key="${CSS.escape(String(key))}"]`)
            ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
        }}
        className={css({
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          minHeight: 0,
          // the tabs and the panels show their own focus
          '&[data-focus-visible]': { outline: 'none!' },
        })}
      >
        <div
          className={css({ borderBottom: '1px solid {colors.border.subtle}' })}
        >
          <TabList
            ref={tabList.ref}
            aria-label={t('tabsLabel')}
            {...tabList.hint}
            className={css({
              display: 'flex',
              gap: { base: '0.125rem', lg: '0.25rem' },
              overflowX: 'auto',
              scrollbarWidth: 'none',
              scrollPaddingX: '2rem',
              paddingX: { base: '0.5rem', md: '1.25rem', lg: '1.75rem' },
              paddingBottom: '0.75rem',
              '&[data-focus-visible]': { outline: 'none!' },
              // the tabs go on past an edge: fade it
              '&[data-overflow-end]': {
                maskImage:
                  'linear-gradient(to right, black calc(100% - 2.5rem), transparent)',
              },
              '&[data-overflow-start]': {
                maskImage:
                  'linear-gradient(to left, black calc(100% - 2.5rem), transparent)',
              },
              '&[data-overflow-start][data-overflow-end]': {
                maskImage:
                  'linear-gradient(to right, transparent, black 2.5rem, black calc(100% - 2.5rem), transparent)',
              },
            })}
          >
            <Tab id="chat" className={tabClass}>
              <TabLabel label={t('tabs.chat')} count={call.messages_count} />
            </Tab>
            <Tab id="transcript" className={tabClass}>
              <TabLabel label={t('tabs.transcript')} />
            </Tab>
            <Tab id="recordings" className={tabClass}>
              <TabLabel
                label={t('tabs.recordings')}
                count={call.recordings.length}
              />
            </Tab>
            <Tab id="participants" className={tabClass}>
              <TabLabel
                label={t('tabs.participants')}
                count={call.participants_count}
              />
            </Tab>
          </TabList>
        </div>
        <TabPanel id="chat" className={panelClass}>
          <ChatHistory call={call} />
        </TabPanel>
        <TabPanel id="transcript" className={panelClass}>
          <TranscriptViewer
            call={call}
            title={title}
            headerLines={headerLines}
          />
        </TabPanel>
        <TabPanel id="recordings" className={panelClass}>
          <CallRecordingsList recordings={call.recordings} />
        </TabPanel>
        <TabPanel id="participants" className={panelClass}>
          <CallParticipantsList call={call} />
        </TabPanel>
      </Tabs>
    </DetailFrame>
  )
}
