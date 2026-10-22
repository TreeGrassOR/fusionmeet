import { css } from '@/styled-system/css'
import { Screen } from '@/layout/Screen'
import { CallDetail } from './CallDetail'
import { CallListPanel } from './CallListPanel'
import { HomeActions } from './HomeActions'
import { NavRail } from './NavRail'

/**
 * Home of a logged-in user, like Yandex Telemost: the navigation, the calls
 * and, on the right, what can be started or the selected call. On a narrow
 * screen the list and the call are shown one at a time.
 */
export const CallsHome = ({ callId }: { callId?: string }) => {
  const isCallSelected = !!callId
  return (
    <Screen header={false} footer={false}>
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          minHeight: 0,
          height: '100%',
          backgroundColor: 'surface.page',
          color: 'fg',
          md: {
            display: 'grid',
            gridTemplateColumns: '4.5rem 20rem minmax(0, 1fr)',
            gridTemplateRows: 'minmax(0, 1fr)',
          },
          lg: { gridTemplateColumns: '4.5rem 22.5rem minmax(0, 1fr)' },
        })}
      >
        <div
          className={css({
            display: isCallSelected ? 'none' : 'flex',
            flexDirection: 'column',
            md: { display: 'flex' },
          })}
        >
          <NavRail />
        </div>
        <div
          className={css({
            display: isCallSelected ? 'none' : 'flex',
            flexDirection: 'column',
            flex: 1,
            minHeight: 0,
            md: { display: 'flex' },
          })}
        >
          <CallListPanel selectedCallId={callId} />
        </div>
        <div
          className={css({
            display: isCallSelected ? 'flex' : 'none',
            flexDirection: 'column',
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            md: { display: 'flex' },
          })}
        >
          {callId ? (
            <CallDetail key={callId} callId={callId} />
          ) : (
            <HomeActions />
          )}
        </div>
      </div>
    </Screen>
  )
}
