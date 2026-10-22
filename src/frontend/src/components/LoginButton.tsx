import { LinkButton } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { authUrl } from '@/features/auth/utils/authUrl'

export const LoginButton = () => {
  const { t } = useTranslation('global', { keyPrefix: 'login' })

  return (
    <LinkButton href={authUrl()} data-attr="login" variant="primary">
      {t('buttonLabel')}
    </LinkButton>
  )
}
