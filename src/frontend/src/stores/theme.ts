import { proxy, subscribe } from 'valtio'
import { STORAGE_KEYS } from '@/utils/storageKeys'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_PREFERENCE_OPTIONS: ThemePreference[] = [
  'system',
  'light',
  'dark',
]

export const DEFAULT_THEME_PREFERENCE: ThemePreference = 'system'

export const parseThemePreference = (raw: unknown): ThemePreference =>
  THEME_PREFERENCE_OPTIONS.includes(raw as ThemePreference)
    ? (raw as ThemePreference)
    : DEFAULT_THEME_PREFERENCE

export const resolveTheme = (
  preference: ThemePreference,
  systemPrefersDark: boolean
): ResolvedTheme =>
  preference === 'system' ? (systemPrefersDark ? 'dark' : 'light') : preference

// Resolve localStorage inside the try: reading window.localStorage itself
// throws a SecurityError when the browser blocks site data.
export const readThemePreference = (
  storage?: Pick<Storage, 'getItem'>
): ThemePreference => {
  try {
    const source = storage ?? globalThis.localStorage
    return parseThemePreference(source?.getItem(STORAGE_KEYS.THEME))
  } catch {
    return DEFAULT_THEME_PREFERENCE
  }
}

export const writeThemePreference = (
  preference: ThemePreference,
  storage?: Pick<Storage, 'setItem'>
) => {
  try {
    const target = storage ?? globalThis.localStorage
    target?.setItem(STORAGE_KEYS.THEME, preference)
  } catch {
    // private mode or blocked storage: keep the in-memory choice
  }
}

export const themeStore = proxy<{ preference: ThemePreference }>({
  preference: readThemePreference(),
})

subscribe(themeStore, () => {
  writeThemePreference(themeStore.preference)
})
