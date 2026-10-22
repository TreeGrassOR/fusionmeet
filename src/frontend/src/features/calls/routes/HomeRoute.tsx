import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'wouter'
import { useConfig } from '@/api/useConfig'
import { useUser } from '@/features/auth/api/useUser'
import Home from '@/features/home/routes/Home'
import { LoginPrompt } from '@/features/recording/components/LoginPrompt'
import { LoadingScreen } from '@/components/LoadingScreen'
import { NotFoundScreen } from '@/components/NotFoundScreen'
import { Screen } from '@/layout/Screen'

// Yandex Telemost-like home, loaded apart
const CallsHome = lazy(() =>
  import('../components/CallsHome').then((module) => ({
    default: module.CallsHome,
  }))
)

/**
 * "/" and "/calls/<id>": a logged-in user with the call history enabled gets
 * the calls (with that call selected), other visitors the home of upstream.
 * Both paths render CallsHome at the same place, so opening a call or going
 * back keeps the list mounted (its focus, scroll and data).
 */
const HomeRoute = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'login' })
  const { callId } = useParams<{ callId?: string }>()
  const { isLoggedIn } = useUser()
  const { data: config, isLoading: isConfigLoading } = useConfig()

  if (isLoggedIn && config?.calls?.enabled) {
    return (
      <Suspense fallback={<LoadingScreen header={false} footer={false} />}>
        <CallsHome callId={callId} />
      </Suspense>
    )
  }

  if (!callId) return <Home />

  // a call of the history, not shown to this visitor
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
  return <NotFoundScreen />
}

export default HomeRoute
