import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import { Field, H } from '@/primitives'
import {
  parseThemePreference,
  themeStore,
  THEME_PREFERENCE_OPTIONS,
} from '@/stores/theme'

/**
 * Light / dark theme choice, stored in this browser. Rendered in both
 * settings dialogs (home and room), right after the language field.
 */
export const AppearanceFields = () => {
  // No suspense: a namespace loading in the middle of the room would blank
  // the whole app (the only Suspense boundary is at the root). Render
  // nothing until it has loaded, rather than the raw keys.
  const { t, ready } = useTranslation('appearance', { useSuspense: false })
  const { preference } = useSnapshot(themeStore)

  if (!ready) return null

  return (
    <>
      <H lvl={2}>{t('heading')}</H>
      <Field
        type="radioGroup"
        label={t('theme.label')}
        description={t('theme.description')}
        value={preference}
        onChange={(value) => {
          themeStore.preference = parseThemePreference(value)
        }}
        items={THEME_PREFERENCE_OPTIONS.map((value) => ({
          value,
          label: t(`theme.options.${value}`),
        }))}
      />
    </>
  )
}
