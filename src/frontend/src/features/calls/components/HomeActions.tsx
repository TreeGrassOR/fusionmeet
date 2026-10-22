import { forwardRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Button as RACButton,
  type ButtonProps as RACButtonProps,
  DialogTrigger,
} from 'react-aria-components'
import {
  type RemixiconComponentType,
  RiCalendarEventLine,
  RiChatNewLine,
  RiCheckLine,
  RiFileCopyLine,
  RiLink,
  RiLoginBoxLine,
  RiUserAddLine,
  RiVideoAddLine,
} from '@remixicon/react'
import { css } from '@/styled-system/css'
import { Button, Text } from '@/primitives'
import { srOnly } from '@/styles/a11y'
import { JoinMeetingDialog } from '@/features/home/components/JoinMeetingDialog'
import { LaterMeetingDialog } from '@/features/home/components/LaterMeetingDialog'
import { useCreateCall } from '../hooks/useCreateCall'
import { useInviteLink } from '../hooks/useInviteLink'
import { ScheduleDialog } from './ScheduleDialog'

const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Fusion Meet'

// Cards are tinted with the accent over the raised surface, in both themes
// (literal values: panda extracts the styles statically)
const cardClass = css({
  position: 'relative',
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'space-between',
  alignItems: 'flex-start',
  gap: '1rem',
  minHeight: '8.5rem',
  padding: { base: '1.125rem 1.25rem', lg: '1.375rem 1.5rem' },
  borderRadius: '20px',
  border: '1px solid',
  borderColor: 'color-mix(in srgb, {colors.accent} 16%, transparent)',
  backgroundColor:
    'color-mix(in srgb, {colors.accent} 7%, {colors.surface.raised})',
  color: 'fg',
  textAlign: 'left',
  cursor: 'pointer',
  transition: 'background 150ms, border-color 150ms, transform 150ms',
  outline: 'none',
  '&[data-hovered]': {
    backgroundColor:
      'color-mix(in srgb, {colors.accent} 12%, {colors.surface.raised})',
    borderColor: 'color-mix(in srgb, {colors.accent} 28%, transparent)',
  },
  '&[data-pressed]': {
    backgroundColor:
      'color-mix(in srgb, {colors.accent} 16%, {colors.surface.raised})',
    transform: 'scale(0.99)',
  },
  '&[data-focus-visible]': {
    outline: '2px solid {colors.focusRing}',
    outlineOffset: '2px',
  },
  '&[data-disabled]': {
    cursor: 'default',
    backgroundColor:
      'color-mix(in srgb, {colors.accent} 4%, {colors.surface.raised})',
    color: 'fg.muted',
  },
})

const compactCardClass = css({
  display: 'flex',
  // below 360 px the label needs the whole width of the card
  flexDirection: { base: 'column', xs: 'row' },
  alignItems: { base: 'flex-start', xs: 'center' },
  gap: { base: '0.375rem', xs: '0.625rem' },
  minHeight: '3.5rem',
  padding: '0.625rem 0.875rem',
  borderRadius: '14px',
  border: '1px solid',
  borderColor: 'color-mix(in srgb, {colors.accent} 16%, transparent)',
  backgroundColor:
    'color-mix(in srgb, {colors.accent} 7%, {colors.surface.raised})',
  color: 'fg',
  fontSize: '0.875rem',
  fontWeight: 600,
  lineHeight: 1.2,
  textAlign: 'left',
  cursor: 'pointer',
  outline: 'none',
  '&[data-hovered]': {
    backgroundColor:
      'color-mix(in srgb, {colors.accent} 12%, {colors.surface.raised})',
  },
  '&[data-pressed]': {
    backgroundColor:
      'color-mix(in srgb, {colors.accent} 16%, {colors.surface.raised})',
  },
  '&[data-focus-visible]': {
    outline: '2px solid {colors.focusRing}',
    outlineOffset: '2px',
  },
  '&[data-disabled]': { cursor: 'default', color: 'fg.muted' },
})

const iconClass = css({ color: 'accent.text', flexShrink: 0 })

type ActionCardProps = Omit<RACButtonProps, 'children'> & {
  icon: RemixiconComponentType
  label: string
  badge?: string
  compact?: boolean
}

const ActionCard = forwardRef<HTMLButtonElement, ActionCardProps>(
  ({ icon: Icon, label, badge, compact = false, ...props }, ref) => (
    <RACButton
      ref={ref}
      className={compact ? compactCardClass : cardClass}
      {...props}
    >
      <Icon size={compact ? 22 : 30} aria-hidden="true" className={iconClass} />
      <span
        className={css({
          fontSize: compact ? undefined : '1.1875rem',
          fontWeight: 600,
          lineHeight: 1.25,
          maxWidth: '12rem',
          minWidth: 0,
          textWrap: 'balance',
          // a long word of a translation rather than past the card
          overflowWrap: 'break-word',
          hyphens: 'auto',
        })}
      >
        {label}
      </span>
      {badge && (
        <span
          className={css({
            position: 'absolute',
            top: '1.25rem',
            right: '1.25rem',
            paddingX: '0.5rem',
            paddingY: '0.125rem',
            borderRadius: 'full',
            backgroundColor: 'accent.subtle',
            color: 'accent.text',
            fontSize: '0.75rem',
            fontWeight: 600,
          })}
        >
          {badge}
        </span>
      )}
    </RACButton>
  )
)
ActionCard.displayName = 'ActionCard'

const InviteIllustration = () => (
  <div
    aria-hidden="true"
    className={css({
      position: 'relative',
      flexShrink: 0,
      width: '6rem',
      height: '6rem',
      borderRadius: 'full',
      display: { base: 'none', lg: 'flex' },
      alignItems: 'center',
      justifyContent: 'center',
      background:
        'radial-gradient(circle at 35% 30%, color-mix(in srgb, {colors.accent} 30%, {colors.surface.raised}), color-mix(in srgb, {colors.accent} 10%, {colors.surface.raised}))',
      color: 'accent.text',
    })}
  >
    <RiUserAddLine size={42} />
    <span
      className={css({
        position: 'absolute',
        right: '-0.25rem',
        bottom: '0.125rem',
        width: '2.25rem',
        height: '2.25rem',
        borderRadius: 'full',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'accent',
        color: 'fg.onAccent',
        border: '3px solid',
        borderColor: 'surface.page',
      })}
    >
      <RiLink size={18} />
    </span>
  </div>
)

const InviteLinkCard = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'home.invite' })
  const invite = useInviteLink()

  return (
    <section
      aria-labelledby="calls-invite-heading"
      className={css({
        display: 'flex',
        alignItems: 'center',
        gap: '1.75rem',
        padding: { base: '1.25rem', sm: '1.5rem 2rem' },
        borderRadius: '20px',
        border: '1px solid {colors.border}',
        backgroundColor: 'surface.raised',
      })}
    >
      <InviteIllustration />
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: '0.25rem',
          minWidth: 0,
        })}
      >
        <h2
          id="calls-invite-heading"
          className={css({ fontSize: '1.125rem', fontWeight: 600 })}
        >
          {t('title', { appTitle: APP_TITLE })}
        </h2>
        <Text variant="note" margin={false}>
          {t('body')}
        </Text>
        <Button
          variant={invite.isCopied ? 'success' : 'primary'}
          size="sm"
          onPress={invite.copyInviteLink}
          isDisabled={invite.isPending}
          className={css({
            marginTop: '0.75rem',
            borderRadius: '10px!',
            paddingX: '0.875rem!',
            paddingY: '0.5rem!',
            whiteSpace: 'nowrap',
          })}
        >
          {invite.isCopied ? (
            <RiCheckLine size={18} aria-hidden="true" />
          ) : (
            <RiFileCopyLine size={18} aria-hidden="true" />
          )}
          {invite.isCopied ? t('copied') : t('copy')}
        </Button>
        {invite.isError && (
          <Text variant="warning" margin={false} role="alert">
            {t('error')}
          </Text>
        )}
      </div>
      <LaterMeetingDialog
        room={invite.fallbackRoom}
        onOpenChange={invite.closeFallback}
      />
    </section>
  )
}

const CompactInviteCard = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'home' })
  const invite = useInviteLink()
  return (
    <>
      <ActionCard
        compact
        icon={invite.isCopied ? RiCheckLine : RiLink}
        label={invite.isCopied ? t('invite.copied') : t('invite.short')}
        onPress={invite.copyInviteLink}
        isDisabled={invite.isPending}
      />
      {invite.isError && (
        // the last card: the message takes the row below the grid
        <Text
          variant="warning"
          role="alert"
          margin={false}
          className={css({ gridColumn: '1 / -1' })}
        >
          {t('invite.error')}
        </Text>
      )}
      <LaterMeetingDialog
        room={invite.fallbackRoom}
        onOpenChange={invite.closeFallback}
      />
    </>
  )
}

const Glow = ({ children }: { children: ReactNode }) => (
  <div
    className={css({
      position: 'relative',
      isolation: 'isolate',
      _before: {
        content: '""',
        position: 'absolute',
        inset: '-18% -12%',
        zIndex: -1,
        pointerEvents: 'none',
        background:
          'radial-gradient(closest-side, color-mix(in srgb, {colors.accent} 13%, transparent), transparent)',
      },
    })}
  >
    {children}
  </div>
)

/**
 * What the user can start from the call history: a call now, joining a
 * call, a scheduled call, an invitation link. `compact` is the narrow-screen
 * version shown above the list.
 */
export const HomeActions = ({ compact = false }: { compact?: boolean }) => {
  const { t } = useTranslation('calls', { keyPrefix: 'home' })
  const { startCall, isPending, isError } = useCreateCall()

  const heading = t('title', { appTitle: APP_TITLE })

  if (compact) {
    return (
      <div>
        <h1 className={srOnly}>{heading}</h1>
        <div
          className={css({
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: '0.5rem',
          })}
        >
          <ActionCard
            compact
            icon={RiVideoAddLine}
            label={t('newCall')}
            onPress={startCall}
            isDisabled={isPending}
          />
          <DialogTrigger>
            <ActionCard compact icon={RiLoginBoxLine} label={t('join')} />
            <JoinMeetingDialog />
          </DialogTrigger>
          <DialogTrigger>
            <ActionCard
              compact
              icon={RiCalendarEventLine}
              label={t('scheduleShort')}
            />
            <ScheduleDialog />
          </DialogTrigger>
          <CompactInviteCard />
        </div>
        {isError && (
          <Text
            variant="warning"
            role="alert"
            className={css({ marginTop: '0.5rem' })}
          >
            {t('createError')}
          </Text>
        )}
      </div>
    )
  }

  return (
    <div
      className={css({
        display: 'flex',
        flexDirection: 'column',
        gap: '2.5rem',
        width: '100%',
        maxWidth: '48rem',
        marginX: 'auto',
        paddingX: { base: '1.5rem', lg: '2.5rem' },
        paddingY: '3rem',
        marginY: 'auto',
      })}
    >
      <h1
        className={css({
          fontSize: { base: '1.5rem', lg: '1.875rem' },
          fontWeight: 700,
          lineHeight: 1.2,
          textAlign: 'center',
          textWrap: 'balance',
        })}
      >
        {heading}
      </h1>
      <Glow>
        <div
          className={css({
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: { base: '0.75rem', lg: '1rem' },
          })}
        >
          <ActionCard
            icon={RiVideoAddLine}
            label={t('newCall')}
            onPress={startCall}
            isDisabled={isPending}
          />
          <ActionCard
            icon={RiChatNewLine}
            label={t('newChat')}
            badge={t('soon')}
            isDisabled
          />
          <DialogTrigger>
            <ActionCard icon={RiLoginBoxLine} label={t('join')} />
            <JoinMeetingDialog />
          </DialogTrigger>
          <DialogTrigger>
            <ActionCard icon={RiCalendarEventLine} label={t('schedule')} />
            <ScheduleDialog />
          </DialogTrigger>
        </div>
        {isError && (
          <Text
            variant="warning"
            role="alert"
            className={css({ marginTop: '0.75rem', textAlign: 'center' })}
          >
            {t('createError')}
          </Text>
        )}
      </Glow>
      <InviteLinkCard />
    </div>
  )
}
