import { useEffect, useState } from 'react'
import { useSnapshot } from 'valtio'
import { useLocation } from 'wouter'
import { resolveTheme, themeStore, type ResolvedTheme } from '@/stores/theme'
import { documentPictureInPictureStore } from '@/stores/documentPictureInPicture'

const DARK_QUERY = '(prefers-color-scheme: dark)'

// keep in sync with surface.page in panda.config.ts and the script in index.html
export const THEME_COLOR: Record<ResolvedTheme, string> = {
  light: '#FFFFFF',
  dark: '#161622',
}

export const applyResolvedTheme = (doc: Document, theme: ResolvedTheme) => {
  doc.documentElement.dataset.theme = theme
  doc
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', THEME_COLOR[theme])
}

/**
 * SDK pages (/sdk and /sdk/...), with the same rule as the script in
 * index.html. Not useIsSdkContext: it matches any path containing '/sdk',
 * such as the room /sdk-abcd-efg, which would flash dark and then turn light.
 */
export const isSdkPath = (pathname: string) => /^\/sdk(\/|$)/.test(pathname)

const systemPrefersDark = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia(DARK_QUERY).matches

/**
 * The theme in use: the stored preference, resolved against the OS setting.
 * SDK pages are embedded in third-party sites and always stay light.
 */
export function useResolvedTheme(): ResolvedTheme {
  const { preference } = useSnapshot(themeStore)
  const [location] = useLocation()
  const [systemDark, setSystemDark] = useState(systemPrefersDark)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(DARK_QUERY)
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return isSdkPath(location) ? 'light' : resolveTheme(preference, systemDark)
}

/**
 * Put the resolved theme on <html> (and on the Picture-in-Picture window when
 * it is open), so semantic tokens and portals follow it.
 */
export function useApplyTheme() {
  const theme = useResolvedTheme()
  // the snapshot only re-runs the effect when PiP opens or closes
  const { window: pipWindowSnap } = useSnapshot(documentPictureInPictureStore)

  useEffect(() => {
    applyResolvedTheme(document, theme)
  }, [theme])

  useEffect(() => {
    const pipWindow = documentPictureInPictureStore.window
    if (pipWindow && !pipWindow.closed) {
      applyResolvedTheme(pipWindow.document, theme)
    }
  }, [theme, pipWindowSnap])

  return theme
}
