import { RiPhoneLine } from '@remixicon/react'
import { css, cx } from '@/styled-system/css'
import { Avatar } from '@/components/Avatar'
import { avatarColor } from '../utils/colors'

/**
 * Round avatar with initials (or a phone without a name), its colour taken
 * from an id, with an optional "live" dot.
 */
export const CallAvatar = ({
  name,
  colorId,
  size = 44,
  isLive = false,
  className,
}: {
  name?: string
  colorId: string
  size?: number
  isLive?: boolean
  className?: string
}) => (
  <span
    aria-hidden="true"
    className={cx(
      css({ position: 'relative', display: 'inline-flex', flexShrink: 0 }),
      className
    )}
  >
    {name ? (
      <Avatar
        name={name}
        bgColor={avatarColor(colorId)}
        style={{ width: size, height: size }}
      />
    ) : (
      <span
        className={css({
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 'full',
          color: 'white',
        })}
        style={{
          width: size,
          height: size,
          backgroundColor: avatarColor(colorId),
        }}
      >
        <RiPhoneLine size={Math.round(size * 0.45)} />
      </span>
    )}
    {isLive && (
      <span
        className={css({
          position: 'absolute',
          right: '-1px',
          bottom: '-1px',
          width: '0.8rem',
          height: '0.8rem',
          borderRadius: 'full',
          backgroundColor: 'success.500',
          border: '2px solid',
          borderColor: 'surface.page',
        })}
      />
    )}
  </span>
)
