import { type ReactNode } from 'react'
import { css } from '@/styled-system/css'

/**
 * A message centred in a tab of a call: loading, empty, error.
 */
export const PanelState = ({ children }: { children: ReactNode }) => (
  <div
    className={css({
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '0.75rem',
      flex: 1,
      minHeight: '12rem',
      paddingX: '1.5rem',
      paddingY: '2rem',
      textAlign: 'center',
      maxWidth: '36rem',
      marginX: 'auto',
    })}
  >
    {children}
  </div>
)
