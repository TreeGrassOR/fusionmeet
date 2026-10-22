import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { DialogTrigger } from 'react-aria-components'
import { Link } from 'wouter'
import {
  RiChat3Fill,
  RiFolderVideoLine,
  RiSettings3Line,
} from '@remixicon/react'
import { css } from '@/styled-system/css'
import { BrandLogo } from '@/components/BrandLogo'
import { Avatar } from '@/components/Avatar'
import { Button, Menu, MenuList } from '@/primitives'
import { SettingsDialog } from '@/features/settings'
import { useUser } from '@/features/auth/api/useUser'
import { logout } from '@/features/auth/utils/logout'
import {
  MY_RECORDINGS_MENU_VALUE,
  useMyRecordingsMenuItems,
} from '@/features/transcription/hooks/useMyRecordingsMenuItems'
import { useMediaQuery } from '@/features/rooms/livekit/hooks/useMediaQuery'
import { getRoutePath } from '@/navigation/getRoutePath'
import { navigateTo } from '@/navigation/navigateTo'
import { avatarColor } from '../utils/colors'

const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Fusion Meet'

// keep in sync with the md breakpoint of panda.config.ts
const WIDE_QUERY = '(min-width: 48em)'

const railItem = css({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '0.125rem',
  width: '3.5rem',
  height: '3.25rem',
  borderRadius: '12px',
  color: 'fg.muted',
  fontSize: '0.6875rem',
  fontWeight: 500,
  lineHeight: 1.2,
  textDecoration: 'none',
  transition: 'background 150ms, color 150ms',
  _hover: { backgroundColor: 'surface.hover', color: 'fg' },
  '&[aria-current=page]': {
    backgroundColor: 'accent.subtle',
    color: 'accent.text',
  },
  _focusVisible: {
    outline: '2px solid {colors.focusRing}',
    outlineOffset: '1px',
  },
  '& svg': { width: '1.375rem', height: '1.375rem', flexShrink: 0 },
})

const RailLink = ({
  href,
  icon,
  label,
  isCurrent,
}: {
  href: string
  icon: ReactNode
  label: string
  isCurrent?: boolean
}) => (
  <li>
    <Link
      href={href}
      className={railItem}
      aria-current={isCurrent ? 'page' : undefined}
    >
      {icon}
      <span>{label}</span>
    </Link>
  </li>
)

const UserMenu = ({ placement }: { placement: 'right' | 'bottom' }) => {
  const { t } = useTranslation()
  const { user } = useUser()
  const myRecordingsMenuItems = useMyRecordingsMenuItems()
  if (!user) return null

  const userLabel = user.full_name || user.email
  const tooltip = t('loggedInUserTooltip')
  const ariaLabel = userLabel ? `${tooltip} ${userLabel}` : tooltip

  return (
    <Menu placement={placement}>
      <Button
        variant="quaternaryText"
        round
        size="xs"
        aria-label={ariaLabel}
        tooltip={userLabel || tooltip}
        className={css({ padding: '0.125rem!' })}
      >
        <Avatar
          name={userLabel}
          bgColor={avatarColor(user.id)}
          style={{ width: 36, height: 36, cursor: 'pointer' }}
        />
      </Button>
      <MenuList
        variant="light"
        items={[
          ...myRecordingsMenuItems,
          { value: 'logout', label: t('logout') },
        ]}
        onAction={(value) => {
          if (value === MY_RECORDINGS_MENU_VALUE) navigateTo('myRecordings')
          if (value === 'logout') logout()
        }}
      />
    </Menu>
  )
}

const SettingsRailButton = () => {
  const { t } = useTranslation('settings')
  return (
    <DialogTrigger>
      <Button
        variant="quaternaryText"
        square
        aria-label={t('settingsButtonLabel')}
        tooltip={t('settingsButtonLabel')}
        className={css({ borderRadius: '12px!' })}
      >
        <RiSettings3Line size={22} aria-hidden="true" />
      </Button>
      <SettingsDialog />
    </DialogTrigger>
  )
}

/**
 * The navigation of the call history: a column of icons on the left of wide
 * screens, a bar on top of narrow ones.
 */
export const NavRail = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'rail' })
  const hasRecordings = useMyRecordingsMenuItems().length > 0
  const isWide = useMediaQuery(WIDE_QUERY)

  return (
    <nav
      aria-label={t('label')}
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '0.25rem',
        flexShrink: 0,
        paddingX: '0.75rem',
        height: '3.5rem',
        borderBottom: '1px solid {colors.border.subtle}',
        backgroundColor: 'surface.sunken',
        md: {
          flex: 1,
          flexDirection: 'column',
          height: 'auto',
          paddingX: 0,
          paddingY: '0.75rem',
          gap: '0.5rem',
          borderBottom: 'none',
          borderRight: '1px solid {colors.border.subtle}',
        },
      })}
    >
      <Link
        href="/"
        aria-label={t('home', { appTitle: APP_TITLE })}
        className={css({
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: '12px',
          padding: '0.375rem',
          _hover: { backgroundColor: 'surface.hover' },
          md: { marginBottom: '0.5rem' },
        })}
      >
        <BrandLogo
          compact={isWide}
          className={css({ fontSize: { base: '1.125rem', md: '1.25rem' } })}
        />
      </Link>
      <ul
        className={css({
          display: 'flex',
          gap: '0.25rem',
          marginLeft: 'auto',
          md: { flexDirection: 'column', marginLeft: 0, gap: '0.375rem' },
        })}
      >
        <RailLink
          href="/"
          icon={<RiChat3Fill aria-hidden="true" />}
          label={t('calls')}
          isCurrent
        />
        {hasRecordings && (
          <RailLink
            href={getRoutePath('myRecordings') as string}
            icon={<RiFolderVideoLine aria-hidden="true" />}
            label={t('recordings')}
          />
        )}
      </ul>
      <div
        className={css({
          display: 'flex',
          alignItems: 'center',
          gap: '0.25rem',
          md: { flexDirection: 'column', marginTop: 'auto', gap: '0.5rem' },
        })}
      >
        <SettingsRailButton />
        <UserMenu placement={isWide ? 'right' : 'bottom'} />
      </div>
    </nav>
  )
}
