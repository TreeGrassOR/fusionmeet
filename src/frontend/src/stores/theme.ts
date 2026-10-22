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

export const readThemePreference = (
  storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage
): ThemePreference => {
  try {
    return parseThemePreference(storage?.getItem(STORAGE_KEYS.THEME))
  } catch {
    return DEFAULT_THEME_PREFERENCE
  }
}

export const writeThemePreference = (
  preference: ThemePreference,
  storage: Pick<Storage, 'setItem'> | undefined = globalThis.localStorage
) => {
  try {
    storage?.setItem(STORAGE_KEYS.THEME, preference)
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
