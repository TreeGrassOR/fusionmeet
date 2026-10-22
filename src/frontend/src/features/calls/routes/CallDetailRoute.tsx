import { useTranslation } from 'react-i18next'
import { useParams } from 'wouter'
import { useConfig } from '@/api/useConfig'
import { useUser } from '@/features/auth/api/useUser'
import { LoginPrompt } from '@/features/recording/components/LoginPrompt'
import { LoadingScreen } from '@/components/LoadingScreen'
import { NotFoundScreen } from '@/components/NotFoundScreen'
import { Screen } from '@/layout/Screen'
import { CallsHome } from '../components/CallsHome'

/**
 * A call of the history (/calls/<id>): the logged-in home with that call
 * selected.
 */
const CallDetailRoute = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'login' })
  const { callId } = useParams()
  const { isLoggedIn } = useUser()
  const { data: config, isLoading: isConfigLoading } = useConfig()

  if (isLoggedIn === undefined || isConfigLoading) {
    return <LoadingScreen header={false} footer={false} />
  }

  if (isLoggedIn === false) {
    return (
      <Screen layout="centered" footer={false}>
        <LoginPrompt heading={t('heading')} body={t('body')} />
      </Screen>
    )
  }

  if (!config?.calls?.enabled || !callId) return <NotFoundScreen />

  return <CallsHome callId={callId} />
}

export default CallDetailRoute
