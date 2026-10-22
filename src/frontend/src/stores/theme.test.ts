import { describe, expect, it } from 'vitest'
import indexHtml from '../../index.html?raw'
import {
  parseThemePreference,
  readThemePreference,
  resolveTheme,
  THEME_PREFERENCE_OPTIONS,
  writeThemePreference,
  type ResolvedTheme,
} from './theme'
import { STORAGE_KEYS } from '@/utils/storageKeys'
import { THEME_COLOR } from '@/hooks/useApplyTheme'

const memoryStorage = (initial: Record<string, string> = {}) => {
  const data = new Map(Object.entries(initial))
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value)
    },
  }
}

const throwingStorage = {
  getItem: () => {
    throw new Error('blocked')
  },
  setItem: () => {
    throw new Error('blocked')
  },
}

/**
 * Run the anti-flash script of index.html against a fake browser and return
 * the data-theme it sets and the theme-color it leaves.
 */
const runInlineScript = ({
  stored,
  systemDark,
  pathname = '/',
  storageThrows = false,
}: {
  stored: string | null
  systemDark: boolean
  pathname?: string
  storageThrows?: boolean
}) => {
  const match = indexHtml.match(/<script>([\s\S]*?)<\/script>/)
  if (!match) throw new Error('inline theme script not found in index.html')
  const attributes: Record<string, string> = {}
  let themeColor = '#FFFFFF'
  const localStorage = storageThrows
    ? throwingStorage
    : memoryStorage(stored === null ? {} : { [STORAGE_KEYS.THEME]: stored })
  const document = {
    documentElement: {
      setAttribute: (name: string, value: string) => {
        attributes[name] = value
      },
    },
    querySelector: () => ({
      setAttribute: (_: string, value: string) => {
        themeColor = value
      },
    }),
  }
  const window = { matchMedia: () => ({ matches: systemDark }) }
  new Function('localStorage', 'window', 'location', 'document', match[1])(
    localStorage,
    window,
    { pathname },
    document
  )
  return { theme: attributes['data-theme'], themeColor }
}

describe('theme preference', () => {
  it('resolves system against the OS preference', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })

  it('falls back to system on unknown values', () => {
    expect(parseThemePreference(null)).toBe('system')
    expect(parseThemePreference('"dark"')).toBe('system')
    expect(parseThemePreference('Dark')).toBe('system')
    expect(parseThemePreference('dark')).toBe('dark')
    expect(parseThemePreference('light')).toBe('light')
  })

  it('stores the preference as a plain string', () => {
    const storage = memoryStorage()
    writeThemePreference('dark', storage)
    expect(storage.data.get(STORAGE_KEYS.THEME)).toBe('dark')
    expect(readThemePreference(storage)).toBe('dark')
  })

  it('survives blocked storage', () => {
    expect(readThemePreference(throwingStorage)).toBe('system')
    expect(() => writeThemePreference('light', throwingStorage)).not.toThrow()
    expect(readThemePreference(undefined)).toBe('system')
  })
})

describe('index.html anti-flash script', () => {
  const stored = [null, 'garbage', ...THEME_PREFERENCE_OPTIONS]

  it.each(stored.flatMap((s) => [true, false].map((d) => [s, d] as const)))(
    'agrees with resolveTheme (stored %s, system dark %s)',
    (value, systemDark) => {
      const expected: ResolvedTheme = resolveTheme(
        parseThemePreference(value),
        systemDark
      )
      const { theme, themeColor } = runInlineScript({
        stored: value,
        systemDark,
      })
      expect(theme).toBe(expected)
      expect(themeColor).toBe(THEME_COLOR[expected])
    }
  )

  it('keeps SDK pages light', () => {
    expect(
      runInlineScript({
        stored: 'dark',
        systemDark: true,
        pathname: '/sdk/create-button',
      }).theme
    ).toBe('light')
    expect(
      runInlineScript({ stored: 'dark', systemDark: true, pathname: '/sdkx' })
        .theme
    ).toBe('dark')
  })

  it('does nothing when storage is blocked', () => {
    expect(
      runInlineScript({ stored: null, systemDark: true, storageThrows: true })
        .theme
    ).toBeUndefined()
  })
})
