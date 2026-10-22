import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'
import { RiMicLine, RiVideoLine } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { Text } from '@/primitives'
import { buttonRecipe } from '@/primitives/buttonRecipe'
import { RecordingMode } from '@/features/recording/types'
import { getRoutePath } from '@/navigation/getRoutePath'
import type { CallRecordingApi } from '../api/types'
import { getRecordingState } from '../utils/recordings'
import { PanelState } from './PanelState'

const rowClass = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.875rem',
  paddingY: '0.875rem',
  borderBottom: '1px solid {colors.border.subtle}',
  _last: { borderBottom: 'none' },
})

const iconClass = css({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '2.5rem',
  height: '2.5rem',
  borderRadius: '12px',
  backgroundColor: 'accent.subtle',
  color: 'accent.text',
  flexShrink: 0,
})

/**
 * The recordings of a call the user can access, with a link to each.
 */
export const CallRecordingsList = ({
  recordings,
}: {
  recordings: CallRecordingApi[]
}) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'recordings' })
  const timeFormat = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { timeStyle: 'short' }),
    [i18n.language]
  )

  if (!recordings.length) {
    return (
      <PanelState>
        <Text variant="note" margin={false} wrap="balance">
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
      {recordings.map((recording) => {
        const isVideo = recording.mode === RecordingMode.ScreenRecording
        const Icon = isVideo ? RiVideoLine : RiMicLine
        const state = getRecordingState(recording.status)
        return (
          <li key={recording.id} className={rowClass}>
            <span className={iconClass} aria-hidden="true">
              <Icon size={20} />
            </span>
            <div className={css({ flex: 1, minWidth: 0 })}>
              <Text variant="body" margin={false} bold>
                {isVideo ? t('video') : t('audio')}
              </Text>
              <Text variant="smNote" margin={false}>
                {t('startedAt', {
                  time: timeFormat.format(new Date(recording.created_at)),
                })}
                {' · '}
                {t(`status.${state}`)}
              </Text>
            </div>
            {/* the other recordings have nothing to show yet, or ever */}
            {state === 'ready' && (
              <Link
                href={getRoutePath('recordingDownload', recording.id) as string}
                className={buttonRecipe({ variant: 'secondary', size: 'sm' })}
                aria-label={isVideo ? t('openVideo') : t('openAudio')}
              >
                {t('open')}
              </Link>
            )}
          </li>
        )
      })}
    </ul>
  )
}
