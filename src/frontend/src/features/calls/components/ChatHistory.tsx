import { Fragment, useLayoutEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { Button, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { ChatMessageBody } from '@/features/chat/components/ChatMessageBody'
import { useCallMessages } from '../api/listCallMessages'
import type { CallDetailApi } from '../api/types'
import { groupMessages } from '../utils/chatGrouping'
import { CallAvatar } from './CallAvatar'
import { PanelState } from './PanelState'

// below this distance from the bottom, new messages scroll into view
const STICK_TO_BOTTOM_PX = 80

const AVATAR_SIZE = 32

/**
 * The chat of a call, read-only, grouped like the in-call chat.
 */
export const ChatHistory = ({ call }: { call: CallDetailApi }) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'chat' })
  const messagesQuery = useCallMessages(call.id, call.is_active)
  const rows = useMemo(
    () => groupMessages(messagesQuery.data ?? []),
    [messagesQuery.data]
  )

  const formatters = useMemo(
    () => ({
      time: new Intl.DateTimeFormat(i18n.language, { timeStyle: 'short' }),
      full: new Intl.DateTimeFormat(i18n.language, {
        dateStyle: 'long',
        timeStyle: 'medium',
      }),
      day: new Intl.DateTimeFormat(i18n.language, {
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      }),
    }),
    [i18n.language]
  )

  // open at the latest messages, follow new ones when already there
  const scrollRef = useRef<HTMLDivElement>(null)
  const wasAtBottom = useRef(true)
  useLayoutEffect(() => {
    const element = scrollRef.current
    if (element && wasAtBottom.current) element.scrollTop = element.scrollHeight
  }, [rows])

  if (messagesQuery.isLoading) {
    return (
      <PanelState>
        <Spinner size={28} />
      </PanelState>
    )
  }
  if (messagesQuery.isError && !rows.length) {
    return (
      <PanelState>
        <Text variant="sm" margin={false} role="alert">
          {t('error')}
        </Text>
        <Button
          variant="secondary"
          size="sm"
          onPress={() => messagesQuery.refetch()}
        >
          {t('retry')}
        </Button>
      </PanelState>
    )
  }
  if (!rows.length) {
    return (
      <PanelState>
        <Text variant="note" margin={false} wrap="balance">
          {call.is_active ? t('emptyActive') : t('empty')}
        </Text>
      </PanelState>
    )
  }

  return (
    <div
      ref={scrollRef}
      onScroll={(event) => {
        const element = event.currentTarget
        wasAtBottom.current =
          element.scrollHeight - element.scrollTop - element.clientHeight <
          STICK_TO_BOTTOM_PX
      }}
      className={css({
        flex: 1,
        minHeight: 0,
        overflowY: 'auto',
        paddingX: { base: '1rem', md: '2rem' },
        paddingY: '1rem',
      })}
    >
      <ol
        aria-label={t('label')}
        className={css({
          display: 'flex',
          flexDirection: 'column',
          maxWidth: '50rem',
        })}
      >
        {rows.map((row) => (
          <Fragment key={row.id}>
            {row.isNewDay && (
              <li
                className={css({
                  alignSelf: 'center',
                  marginY: '0.75rem',
                  paddingX: '0.75rem',
                  paddingY: '0.125rem',
                  borderRadius: 'full',
                  backgroundColor: 'surface.hover',
                  color: 'fg.muted',
                  fontSize: '0.75rem',
                })}
              >
                {formatters.day.format(row.timestamp)}
              </li>
            )}
            <li
              title={formatters.full.format(row.timestamp)}
              className={css({
                display: 'flex',
                gap: '0.75rem',
                paddingTop: row.showHeader ? '0.875rem' : '0.125rem',
              })}
            >
              {row.showHeader ? (
                <CallAvatar
                  name={row.sender_name || '?'}
                  colorId={row.sender_name}
                  size={AVATAR_SIZE}
                />
              ) : (
                <span
                  aria-hidden="true"
                  style={{ width: AVATAR_SIZE, flexShrink: 0 }}
                />
              )}
              <div className={css({ minWidth: 0, flex: 1 })}>
                {row.showHeader && (
                  <div
                    className={css({
                      display: 'flex',
                      alignItems: 'baseline',
                      gap: '0.5rem',
                    })}
                  >
                    <span
                      className={css({
                        fontWeight: 600,
                        fontSize: '0.875rem',
                        color: row.sender_is_me ? 'accent.text' : 'fg',
                      })}
                    >
                      {row.sender_is_me ? t('you') : row.sender_name}
                    </span>
                    <span
                      className={css({
                        fontSize: '0.75rem',
                        color: 'fg.muted',
                      })}
                    >
                      {formatters.time.format(row.timestamp)}
                    </span>
                  </div>
                )}
                <ChatMessageBody message={row.text} />
              </div>
            </li>
          </Fragment>
        ))}
      </ol>
    </div>
  )
}
