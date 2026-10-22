import { useTranslation } from 'react-i18next'
import { GridListItem } from 'react-aria-components'
import { RiFileTextLine, RiVideoLine } from '@remixicon/react'
import { css } from '@/styled-system/css'
import type { CallApi } from '../api/types'
import { getCallTitleParts, formatCallTitle } from '../utils/callTitle'
import { formatCallTimeLabel } from '../utils/dates'
import { formatBadgeCount } from '../utils/badge'
import { CallAvatar } from './CallAvatar'

const itemClass = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.75rem',
  paddingX: '0.75rem',
  paddingY: '0.625rem',
  borderRadius: '14px',
  cursor: 'pointer',
  outline: 'none',
  transition: 'background 150ms',
  '&[data-hovered]': { backgroundColor: 'surface.hover' },
  '&[data-pressed]': { backgroundColor: 'surface.pressed' },
  '&[data-current]': { backgroundColor: 'accent.subtle' },
  '&[data-focus-visible]': {
    outline: '2px solid {colors.focusRing}',
    outlineOffset: '-2px',
  },
})

const ellipsis = css({
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  minWidth: 0,
})

export const CallListItem = ({
  call,
  isCurrent,
  now,
  onOpen,
}: {
  call: CallApi
  isCurrent: boolean
  now: Date
  onOpen: (callId: string) => void
}) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'item' })
  const { t: tCalls } = useTranslation('calls')
  const locale = i18n.language

  const titleParts = getCallTitleParts(call)
  const title = formatCallTitle(titleParts, tCalls, locale, now)
  const avatarName =
    titleParts.kind === 'title'
      ? titleParts.title
      : titleParts.kind === 'names'
        ? titleParts.names.join(' ')
        : undefined

  const lastMessage = call.last_message
  const time = call.is_active
    ? t('active')
    : formatCallTimeLabel(
        new Date(lastMessage?.sent_at ?? call.started_at),
        now,
        locale,
        t('yesterday')
      )
  const sender = lastMessage
    ? lastMessage.is_mine
      ? t('you')
      : lastMessage.sender_name || t('someone')
    : ''
  const preview = lastMessage
    ? `${sender}: ${lastMessage.text}`
    : t('participants', { count: call.participants_count })

  // the row is read as one label: say everything it shows
  const label = [
    title,
    time,
    preview,
    call.unread_count > 0 && t('unread', { count: call.unread_count }),
    call.has_transcript && t('hasTranscript'),
    call.has_recording && t('hasRecording'),
    isCurrent && t('current'),
  ]
    .filter(Boolean)
    .join(', ')

  return (
    <GridListItem
      id={call.id}
      textValue={label}
      className={itemClass}
      data-current={isCurrent || undefined}
      onAction={() => onOpen(call.id)}
    >
      <CallAvatar
        name={avatarName}
        colorId={call.room.id}
        isLive={call.is_active}
      />
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.125rem',
          flex: 1,
          minWidth: 0,
        })}
      >
        <div
          className={css({
            display: 'flex',
            alignItems: 'baseline',
            gap: '0.5rem',
          })}
        >
          <span
            className={`${ellipsis} ${css({
              flex: 1,
              fontWeight: 600,
              fontSize: '0.9375rem',
              color: 'fg',
            })}`}
          >
            {title}
          </span>
          <span
            className={css({
              flexShrink: 0,
              fontSize: '0.75rem',
              color: call.is_active ? 'success.subtle-text' : 'fg.muted',
              fontWeight: call.is_active ? 600 : 400,
            })}
          >
            {time}
          </span>
        </div>
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            gap: '0.375rem',
          })}
        >
          <span
            className={`${ellipsis} ${css({
              flex: 1,
              fontSize: '0.875rem',
              color: 'fg.muted',
            })}`}
          >
            {lastMessage ? (
              <>
                <span className={css({ color: 'fg', fontWeight: 500 })}>
                  {sender}:
                </span>{' '}
                {lastMessage.text}
              </>
            ) : (
              preview
            )}
          </span>
          {call.has_transcript && (
            <RiFileTextLine
              size={15}
              aria-hidden="true"
              className={css({ color: 'fg.muted', flexShrink: 0 })}
            />
          )}
          {call.has_recording && (
            <RiVideoLine
              size={15}
              aria-hidden="true"
              className={css({ color: 'fg.muted', flexShrink: 0 })}
            />
          )}
          {call.unread_count > 0 && (
            <span
              aria-hidden="true"
              className={css({
                flexShrink: 0,
                minWidth: '1.25rem',
                height: '1.25rem',
                paddingX: '0.375rem',
                borderRadius: 'full',
                backgroundColor: 'accent',
                color: 'fg.onAccent',
                fontSize: '0.75rem',
                fontWeight: 600,
                lineHeight: '1.25rem',
                textAlign: 'center',
              })}
            >
              {formatBadgeCount(call.unread_count)}
            </span>
          )}
        </div>
      </div>
    </GridListItem>
  )
}
