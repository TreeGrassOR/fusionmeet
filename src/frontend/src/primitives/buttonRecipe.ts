import { type RecipeVariantProps, cva } from '@/styled-system/css'

export const buttonRecipe = cva({
  base: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    transition: 'background 200ms, outline 200ms, border-color 200ms',
    cursor: 'pointer',
    border: '1px solid transparent',
    '&[data-disabled]': {
      cursor: 'default',
    },
    gap: '0.5rem',
  },
  variants: {
    size: {
      default: {
        borderRadius: 4,
        paddingX: '1',
        paddingY: '0.625',
        '--square-padding': '{spacing.0.625}',
      },
      sm: {
        borderRadius: 4,
        paddingX: '0.5',
        paddingY: '0.25',
        '--square-padding': '{spacing.0.25}',
      },
      xs: {
        borderRadius: 4,
        '--square-padding': '0',
      },
      compact: {
        borderRadius: 4,
        paddingX: '0.5',
        paddingY: '0.625',
        '--square-padding': '{spacing.0.625}',
      },
    },
    square: {
      true: {
        paddingX: 'var(--square-padding)',
        paddingY: 'var(--square-padding)',
      },
    },
    round: {
      true: {
        borderRadius: '50%',
        paddingX: 'var(--square-padding)',
        paddingY: 'var(--square-padding)',
      },
    },
    variant: {
      // light variants follow the theme (semantic tokens), *Dark/*Circle
      // variants are the in-call chrome, dark in both themes
      primary: {
        backgroundColor: 'accent',
        color: 'fg.onAccent',
        fontWeight: 'medium !important',
        '&[data-hovered]': {
          backgroundColor: 'accent.hover',
        },
        '&[data-pressed]': {
          backgroundColor: 'accent.hover',
        },
        '&[data-disabled]': {
          backgroundColor: 'surface.hover',
          color: 'fg.subtle',
        },
      },
      secondary: {
        backgroundColor: 'surface.raised',
        color: 'accent.text',
        fontWeight: 'medium !important',
        borderColor: 'accent',
        '&[data-hovered]': {
          backgroundColor: 'surface.hover',
        },
        '&[data-pressed]': {
          backgroundColor: 'surface.hover',
        },
      },
      secondaryText: {
        backgroundColor: 'transparent',
        fontWeight: 'medium !important',
        color: 'accent.text',
        '&[data-hovered]': {
          backgroundColor: 'surface.hover',
        },
        '&[data-pressed]': {
          backgroundColor: 'surface.hover',
        },
        '&[data-disabled]': {
          color: 'fg.subtle',
        },
      },
      whiteCircle: {
        color: 'white',
        border: '1px white solid',
        width: '56px',
        height: '56px',
        borderRadius: '100%',
        '&[data-hovered]': {
          backgroundColor: 'greyscale.100/20',
        },
        '&[data-pressed]': {
          backgroundColor: 'greyscale.100/50',
        },
      },
      bigSquare: {
        width: '56px',
        height: '56px',
        borderColor: { base: 'greyscale.200', _dark: 'border.subtle' },
        borderRadius: '4px',
        backgroundColor: 'surface.sunken',
        padding: '0',
        flexShrink: 0,
        '&[data-hovered]': {
          backgroundColor: 'surface.hover',
        },
        transition: 'box-shadow 0.2s ease-in-out',
        '&[data-selected]': {
          boxShadow: {
            base: '0 0 0 3px token(colors.primary.600) inset, 0 0 0 5px token(colors.surface.raised) inset',
            _dark:
              '0 0 0 3px token(colors.accent) inset, 0 0 0 5px token(colors.surface.raised) inset',
          },
        },
        '&[data-disabled]': {
          backgroundColor: 'surface.hover',
          color: 'fg.subtle',
          opacity: '0.7',
        },
      },
      tertiary: {
        backgroundColor: 'accent.subtle',
        fontWeight: 'medium !important',
        color: 'accent.text',
        '&[data-hovered]': {
          backgroundColor: 'accent.subtle-hover',
        },
        '&[data-pressed]': {
          backgroundColor: 'accent.subtle-hover',
        },
        '&[data-disabled]': {
          backgroundColor: 'transparent',
          // readable on both themes
          color: 'primary.400',
        },
      },
      tertiaryText: {
        backgroundColor: 'transparent',
        fontWeight: 'medium !important',
        color: { base: 'primary.900', _dark: 'accent.text' },
        '&[data-hovered]': {
          backgroundColor: 'accent.subtle-hover',
        },
        '&[data-pressed]': {
          backgroundColor: 'accent.subtle-hover',
        },
      },
      primaryDark: {
        backgroundColor: 'primaryDark.100',
        fontWeight: 'medium !important',
        color: 'white',
        '&[data-pressed]': {
          backgroundColor: 'primaryDark.900',
          color: 'primaryDark.100',
        },
        '&[data-disabled]': {
          backgroundColor: 'primaryDark.75',
          color: 'primaryDark.300',
        },
        '&[data-hovered]': {
          backgroundColor: 'primaryDark.300',
          color: 'white',
        },
        '&[data-selected]': {
          backgroundColor: 'primaryDark.700 !important',
          color: 'primaryDark.100 !important',
        },
      },
      secondaryDark: {
        backgroundColor: 'primaryDark.50',
        fontWeight: 'medium !important',
        color: 'white',
        '&[data-pressed]': {
          backgroundColor: 'primaryDark.200',
        },
        '&[data-hovered]': {
          backgroundColor: 'primaryDark.100',
          color: 'white',
        },
        '&[data-selected]': {
          backgroundColor: 'primaryDark.700 !important',
          color: 'primaryDark.100 !important',
        },
      },
      primaryTextDark: {
        backgroundColor: 'transparent',
        fontWeight: 'medium !important',
        color: 'white',
        '&[data-hovered]': {
          backgroundColor: 'primaryDark.100',
        },
        '&[data-pressed]': {
          backgroundColor: 'primaryDark.700',
          color: 'primaryDark.100',
        },
        '&[data-selected]': {
          backgroundColor: 'primaryDark.700',
          color: 'primaryDark.100',
        },
        '&[data-focus-visible]': {
          outline: '2px solid',
          outlineColor: 'focusRing',
          outlineOffset: '2px',
        },
        '&[data-disabled]': {
          opacity: 0.2,
        },
      },
      quaternaryText: {
        backgroundColor: 'transparent',
        fontWeight: 'medium !important',
        color: 'fg.muted',
        '&[data-hovered]': {
          backgroundColor: 'surface.hover',
          color: { base: 'greyscale.700', _dark: 'fg' },
        },
        '&[data-pressed]': {
          backgroundColor: 'surface.hover',
          color: { base: 'greyscale.700', _dark: 'fg' },
        },
        '&[data-disabled]': {
          backgroundColor: 'transparent',
          color: { base: 'greyscale.300', _dark: 'fg.subtle' },
        },
      },
      greyscale: {
        backgroundColor: 'transparent',
        color: 'fg.subtle',
        '&[data-hovered]': {
          color: { base: 'greyscale.800', _dark: 'fg' },
        },
        '&[data-pressed]': {
          color: { base: 'greyscale.800', _dark: 'fg' },
        },
        '&[data-selected]': {
          color: { base: 'greyscale.800', _dark: 'fg' },
        },
        '&[data-disabled]': {
          color: { base: 'greyscale.200', _dark: 'greyscale.700' },
        },
      },
      danger: {
        backgroundColor: 'error.400',
        color: 'white',
        '&[data-hovered]': {
          backgroundColor: 'error.600',
        },
        '&[data-pressed]': {
          backgroundColor: 'error.700',
          color: 'error.200',
        },
      },
      error2: {
        backgroundColor: 'error.200',
        color: 'error.900',
        '&[data-hovered]': {
          backgroundColor: 'error.300',
        },
        '&[data-focused]': {
          backgroundColor: 'error.200',
        },
        '&[data-pressed]': {
          backgroundColor: 'error.900',
          color: 'error.100',
        },
        '&[data-selected]': {
          backgroundColor: 'error.900 !important',
          color: 'error.100 !important',
        },
        '&[data-disabled]': {
          backgroundColor: 'error.200 !important',
          color: 'error.300 !important',
        },
      },
      errorCircle: {
        backgroundColor: 'error.500',
        width: '56px',
        height: '56px',
        borderRadius: '100%',
        color: 'white',
        '&[data-hovered]': {
          backgroundColor: 'error.600',
        },
        '&[data-pressed]': {
          backgroundColor: 'error.700',
          color: 'error.200',
        },
      },
      // @TODO: better handling of colors… this is a mess
      success: {
        colorPalette: 'success',
        color: 'success.subtle-text',
        backgroundColor: 'success.subtle',
        '&[data-hovered]': {
          backgroundColor: { base: 'success.200', _dark: 'success.900' },
        },
        '&[data-pressed]': {
          backgroundColor: 'success.subtle!',
        },
      },
      text: {
        color: 'primary',
        '&[data-hovered]': {
          background: {
            base: 'gray.100 !important',
            _dark: 'surface.hover !important',
          },
          color: 'primary !important',
        },
      },
      permission: {
        position: 'relative',
        borderRadius: '100%',
        color: 'amber.500',
        width: 'fit-content',
        height: 'fit-content',
        padding: '0 !important',
        margin: '0 !important',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      },
    },
    invisible: {
      true: {
        borderColor: 'none!',
        backgroundColor: 'none!',
        '&[data-hovered]': {
          backgroundColor: 'none!',
          borderColor: 'colorPalette.active!',
        },
        '&[data-pressed]': {
          borderColor: 'currentcolor',
        },
        '&[data-disabled]': {
          color: { base: 'gray.300', _dark: 'fg.subtle' },
        },
      },
    },
    fullWidth: {
      true: {
        width: 'full',
      },
    },
    loading: {
      true: {},
    },
    // some toggle buttons make more sense without a "pushed button" style when selected because their content changes to mark the state
    shySelected: {
      true: {},
    },
    description: {
      true: {
        flexDirection: 'column',
        gap: '0.5rem',
        '& span': {
          fontSize: '13px',
          textAlign: 'center',
        },
      },
    },
    // if the button is next to other ones to make a "button group", tell where the button is to handle radius
    groupPosition: {
      left: {
        borderTopRightRadius: 0,
        borderBottomRightRadius: 0,
      },
      right: {
        borderTopLeftRadius: 0,
        borderBottomLeftRadius: 0,
        borderLeft: 0,
      },
      center: {
        borderRadius: 0,
      },
    },
  },
  compoundVariants: [
    {
      variant: 'primaryDark',
      shySelected: true,
      css: {
        '&[data-selected]': {
          backgroundColor: 'primaryDark.100',
          color: 'white',
        },
      },
    },
  ],
  defaultVariants: {
    size: 'default',
    variant: 'primary',
  },
})

export type ButtonRecipe = typeof buttonRecipe

export type ButtonRecipeProps = RecipeVariantProps<ButtonRecipe>
