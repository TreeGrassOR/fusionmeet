import { type ReactNode } from 'react'
import {
  Label,
  Slider as RACSlider,
  SliderFill,
  SliderOutput,
  SliderThumb,
  SliderTrack,
  type SliderProps as RACSliderProps,
} from 'react-aria-components'
import { sva } from '@/styled-system/css'

const THUMB_SIZE = '1rem'

const slider = sva({
  slots: [
    'root',
    'header',
    'label',
    'output',
    'track',
    'rail',
    'fill',
    'thumb',
  ],
  base: {
    root: {
      display: 'flex',
      flexDirection: 'column',
      gap: 0.25,
      width: 'full',
      minWidth: '8rem',
      '&[data-disabled]': {
        opacity: 0.5,
      },
    },
    header: {
      display: 'flex',
      alignItems: 'baseline',
      justifyContent: 'space-between',
      gap: 0.5,
      textStyle: 'sm',
    },
    label: {
      fontWeight: 'medium',
    },
    output: {
      marginLeft: 'auto',
      fontVariantNumeric: 'tabular-nums',
    },
    track: {
      position: 'relative',
      // keeps the thumb within the slider bounds at both ends
      marginX: `calc(${THUMB_SIZE} / 2)`,
      height: '1.5rem',
      cursor: 'pointer',
      '&[data-disabled]': {
        cursor: 'not-allowed',
      },
    },
    rail: {
      position: 'absolute',
      top: '50%',
      left: 0,
      right: 0,
      height: '0.25rem',
      transform: 'translateY(-50%)',
      borderRadius: '9999px',
      overflow: 'hidden',
    },
    fill: {
      borderRadius: '9999px',
    },
    thumb: {
      // react-aria sets left and translate(-50%, -50%) inline
      top: '50%',
      width: THUMB_SIZE,
      height: THUMB_SIZE,
      borderRadius: '50%',
      border: '2px solid',
      transition: 'box-shadow 100ms',
      forcedColorAdjust: 'none',
      '&[data-focus-visible]': {
        outline: '2px solid',
        outlineColor: 'focusRing',
        outlineOffset: '2px',
      },
    },
  },
  variants: {
    variant: {
      light: {
        root: {
          color: 'box.text',
        },
        rail: {
          backgroundColor: 'surface.pressed',
        },
        fill: {
          backgroundColor: 'accent',
        },
        thumb: {
          backgroundColor: 'surface.raised',
          borderColor: 'accent',
          '&[data-hovered], &[data-dragging]': {
            boxShadow: '0 0 0 4px {colors.accent.subtle-hover}',
          },
        },
      },
      dark: {
        root: {
          color: 'white',
        },
        rail: {
          backgroundColor: 'primaryDark.200',
        },
        fill: {
          backgroundColor: 'primaryDark.800',
        },
        thumb: {
          backgroundColor: 'white',
          borderColor: 'primaryDark.800',
          '&[data-hovered], &[data-dragging]': {
            boxShadow: '0 0 0 4px {colors.primaryDark.300}',
          },
          '&[data-focus-visible]': {
            outlineColor: 'white',
          },
        },
      },
    },
  },
  defaultVariants: {
    variant: 'light',
  },
})

export type SliderProps = Omit<
  RACSliderProps<number>,
  'children' | 'className' | 'style'
> & {
  /**
   * Visible label. When omitted, pass an `aria-label` instead.
   */
  label?: ReactNode
  /**
   * Display the formatted value (see `formatOptions`) next to the label.
   */
  showOutput?: boolean
  variant?: 'light' | 'dark'
}

/**
 * Styled RAC Slider with a single thumb.
 */
export const Slider = ({
  label,
  showOutput = true,
  variant = 'light',
  ...props
}: SliderProps) => {
  const classes = slider({ variant })
  return (
    <RACSlider {...props} className={classes.root}>
      {(label || showOutput) && (
        <div className={classes.header}>
          {label && <Label className={classes.label}>{label}</Label>}
          {showOutput && <SliderOutput className={classes.output} />}
        </div>
      )}
      <SliderTrack className={classes.track}>
        <div className={classes.rail}>
          <SliderFill className={classes.fill} />
        </div>
        <SliderThumb className={classes.thumb} />
      </SliderTrack>
    </RACSlider>
  )
}
