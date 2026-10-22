import { css } from '@/styled-system/css'
import type { ConnectionTestStepStatus } from '../types'

/**
 * Panda extracts styles statically, so every status needs its own literal
 * `css()` call: `css({ backgroundColor: someVariable })` would emit nothing.
 */
export const statusSquareClass: Record<ConnectionTestStepStatus, string> = {
  pending: css({
    backgroundColor: 'transparent',
    border: '1px solid {colors.greyscale.300}',
  }),
  running: css({
    backgroundColor: 'accent',
    animation: 'pulse_background 1.2s ease-in-out infinite',
  }),
  success: css({ backgroundColor: 'success.600' }),
  warning: css({ backgroundColor: 'warning' }),
  failed: css({ backgroundColor: 'danger.600' }),
  skipped: css({ backgroundColor: 'greyscale.300' }),
}

/**
 * Colour is carried by the square; the label stays near-black except on
 * failure and warning.
 *
 * Grey labels use gray.400 in dark: at least 4.5:1 on the card and on the
 * hovered or expanded header (surface.raised and surface.hover).
 */
export const statusTextClass: Record<ConnectionTestStepStatus, string> = {
  pending: css({ color: { base: 'greyscale.500', _dark: 'gray.400' } }),
  running: css({ color: { base: 'greyscale.700', _dark: 'fg.muted' } }),
  success: css({ color: 'fg' }),
  warning: css({
    color: { base: 'warning', _dark: 'warning.subtle-text' },
    fontWeight: 'medium',
  }),
  failed: css({
    color: { base: 'danger.600', _dark: 'danger.400' },
    fontWeight: 'medium',
  }),
  skipped: css({ color: { base: 'greyscale.500', _dark: 'gray.400' } }),
}
