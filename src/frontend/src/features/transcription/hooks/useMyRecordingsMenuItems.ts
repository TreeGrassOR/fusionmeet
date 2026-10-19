import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useConfig } from '@/api/useConfig'

export const MY_RECORDINGS_MENU_VALUE = 'myRecordings'

/**
 * Entry of the logged-in user menu leading to the "My recordings and
 * transcripts" page. Empty when neither recording nor transcription is
 * enabled on this instance.
 */
export const useMyRecordingsMenuItems = () => {
  const { t, ready } = useTranslation('transcription', {
    keyPrefix: 'myRecordings',
    useSuspense: false,
  })
  const { data } = useConfig()
  const isAvailable =
    data?.recording?.is_enabled === true ||
    data?.transcription?.enabled === true

  return useMemo(
    () =>
      isAvailable && ready
        ? [{ value: MY_RECORDINGS_MENU_VALUE, label: t('menuItem') }]
        : [],
    [isAvailable, ready, t]
  )
}
