import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { css } from '@/styled-system/css'
import { Text } from '@/primitives'
import type { CallDetailApi } from '../api/types'
import { CallAvatar } from './CallAvatar'
import { PanelState } from './PanelState'

const tagClass = css({
  paddingX: '0.5rem',
  paddingY: '0.0625rem',
  borderRadius: 'full',
  backgroundColor: 'surface.hover',
  // fg.muted is below 4.5:1 on surface.hover in the dark theme
  color: {
    base: 'fg.muted',
    _dark: 'color-mix(in srgb, {colors.fg} 30%, {colors.fg.muted})',
  },
  fontSize: '0.75rem',
  fontWeight: 500,
})

/**
 * Who joined a call, when they came and left.
 */
export const CallParticipantsList = ({ call }: { call: CallDetailApi }) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'participants' })
  const timeFormat = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { timeStyle: 'short' }),
    [i18n.language]
  )

  if (!call.participants.length) {
    return (
      <PanelState>
        <Text variant="note" margin={false}>
          {t('none')}
        </Text>
      </PanelState>
    )
  }

  return (
    <ul
      className={css({
        maxWidth: '44rem',
        paddingX: { base: '1rem', md: '2rem' },
        paddingY: '0.5rem',
      })}
    >
      {call.participants.map((participant, index) => {
        const joined = timeFormat.format(new Date(participant.joined_at))
        const time = participant.left_at
          ? t('range', {
              start: joined,
              end: timeFormat.format(new Date(participant.left_at)),
            })
          : call.is_active
            ? t('present', { start: joined })
            : t('since', { start: joined })
        return (
          <li
            key={`${index}-${participant.name}`}
            className={css({
              display: 'flex',
              alignItems: 'center',
              gap: '0.875rem',
              paddingY: '0.625rem',
            })}
          >
            <CallAvatar
              name={participant.name || '?'}
              colorId={participant.name}
              size={40}
              isLive={call.is_active && !participant.left_at}
            />
            <div className={css({ flex: 1, minWidth: 0 })}>
              <div
                className={css({
                  display: 'flex',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '0.5rem',
                })}
              >
                <Text variant="body" margin={false} bold>
                  {participant.name || t('noName')}
                </Text>
                {participant.is_me && (
                  <span className={tagClass}>{t('you')}</span>
                )}
                {participant.is_guest && (
                  <span className={tagClass}>{t('guest')}</span>
                )}
              </div>
              <Text variant="smNote" margin={false}>
                {time}
              </Text>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
