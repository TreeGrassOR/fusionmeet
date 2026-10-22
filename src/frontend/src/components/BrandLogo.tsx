import { css, cx } from '@/styled-system/css'
import { srOnly } from '@/styles/a11y'

const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Fusion Meet'

// primary.500: readable on light and dark surfaces alike
const BRAND_ACCENT = '#6A6AF4'

/**
 * Fusion Meet mark: two overlapping rounded squares, the overlap blends both.
 * The front square is the accent colour, the rest follows `currentColor`, so
 * the mark works on light and dark surfaces. The same geometry is used for the
 * favicons and the images in `public/` (they are rendered from it).
 */
const BrandMark = ({ className }: { className?: string }) => (
  <svg
    viewBox="0 0 32 32"
    aria-hidden="true"
    focusable="false"
    className={className}
  >
    <rect x="2" y="10" width="19" height="19" rx="6.5" fill="currentColor" />
    <rect x="11" y="3" width="19" height="19" rx="6.5" fill={BRAND_ACCENT} />
    <path
      d="M11 10H14.5A6.5 6.5 0 0 1 21 16.5V22H17.5A6.5 6.5 0 0 1 11 15.5Z"
      fill="currentColor"
      opacity="0.5"
    />
  </svg>
)

/**
 * The application logo: the mark and the application name, or the mark alone
 * when `compact` (the name is then kept for screen readers).
 * Size it with `font-size` (the mark scales with the text).
 */
export const BrandLogo = ({
  className,
  compact = false,
}: {
  className?: string
  compact?: boolean
}) => (
  <span
    className={cx(
      css({
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.375em',
        color: 'fg',
        fontSize: { base: '1.25rem', sm: '1.375rem' },
        fontWeight: 600,
        lineHeight: 1,
        letterSpacing: '-0.01em',
        whiteSpace: 'nowrap',
      }),
      className
    )}
  >
    <BrandMark
      className={css({ width: '1.5em', height: '1.5em', flexShrink: 0 })}
    />
    <span className={compact ? srOnly : undefined}>{APP_TITLE}</span>
  </span>
)
