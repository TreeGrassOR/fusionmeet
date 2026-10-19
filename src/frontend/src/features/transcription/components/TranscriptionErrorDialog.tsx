import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import { Button, Dialog, P } from '@/primitives'
import { transcriptionStore } from '../store'

export const TranscriptionErrorDialog = () => {
  const snap = useSnapshot(transcriptionStore)
  const { t, ready } = useTranslation('transcription', {
    keyPrefix: 'errorDialog',
    useSuspense: false,
  })

  if (!ready) return null

  return (
    <Dialog
      isOpen={!!snap.errorDialog}
      role="alertdialog"
      title={t('title')}
      aria-label={t('title')}
    >
      <P>{snap.errorDialog === 'stop' ? t('body.stop') : t('body.start')}</P>
      <Button
        variant="text"
        size="sm"
        onPress={() => (transcriptionStore.errorDialog = '')}
      >
        {t('button')}
      </Button>
    </Dialog>
  )
}
