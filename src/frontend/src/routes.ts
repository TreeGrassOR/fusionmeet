import { flexibleRoomIdPattern } from '@/features/rooms'
import { ComponentType, lazy, LazyExoticComponent } from 'react'

// The home of upstream, or the call history of a logged-in user (see below)
const HomeRoute = lazy(() => import('@/features/calls/routes/HomeRoute'))
const RecordingDownloadRoute = lazy(
  () => import('@/features/recording/routes/RecordingDownload')
)
const CreatePopup = lazy(() => import('@/features/sdk/routes/CreatePopup'))
const CreateMeetingButton = lazy(
  () => import('@/features/sdk/routes/CreateMeetingButton')
)
const SettingsPopup = lazy(() => import('@/features/sdk/routes/SettingsPopup'))
const RoomRoute = lazy(() => import('@/features/rooms/routes/Room'))
const FeedbackRoute = lazy(() => import('@/features/rooms/routes/Feedback'))
const ConnectionTestRoute = lazy(
  () => import('@/features/diagnostics/routes/ConnectionTest')
)
const MyRecordingsRoute = lazy(
  () => import('@/features/transcription/routes/MyRecordings')
)

const roomIdRegex = new RegExp(`^[/](?<roomId>${flexibleRoomIdPattern})$`)

const callIdPattern =
  '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

export const routes: Record<
  | 'home'
  | 'room'
  | 'feedback'
  | 'connectionTest'
  | 'sdkCreatePopup'
  | 'sdkCreateButton'
  | 'sdkSettingsPopup'
  | 'recordingDownload'
  | 'myRecordings'
  | 'call',
  {
    name: RouteName
    path: RegExp | string
    Component: LazyExoticComponent<ComponentType>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    to?: (...args: any[]) => string | URL
  }
> = {
  // "/" and a call of the history ("/calls/<id>") are one route, so that the
  // logged-in home (the list of calls) stays mounted when a call is opened
  home: {
    name: 'home',
    path: new RegExp(`^/(?:calls/(?<callId>${callIdPattern}))?$`),
    to: () => '/',
    Component: HomeRoute,
  },
  room: {
    name: 'room',
    to: (roomId: string) => `/${roomId.trim()}`,
    path: roomIdRegex,
    Component: RoomRoute,
  },
  feedback: {
    name: 'feedback',
    path: '/feedback',
    Component: FeedbackRoute,
  },
  connectionTest: {
    name: 'connectionTest',
    path: '/test-connection',
    Component: ConnectionTestRoute,
  },
  sdkCreatePopup: {
    name: 'sdkCreatePopup',
    path: '/sdk/create-popup',
    Component: CreatePopup,
  },
  sdkCreateButton: {
    name: 'sdkCreateButton',
    path: '/sdk/create-button',
    Component: CreateMeetingButton,
  },
  sdkSettingsPopup: {
    name: 'sdkSettingsPopup',
    path: '/sdk/settings-popup',
    Component: SettingsPopup,
  },
  recordingDownload: {
    name: 'recordingDownload',
    path: /^\/recording\/(?<recordingId>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/,
    to: (recordingId: string) => `/recording/${recordingId.trim()}`,
    Component: RecordingDownloadRoute,
  },
  // Not "/recordings": a one-segment path of 10 letters matches the room route.
  myRecordings: {
    name: 'myRecordings',
    path: '/my-recordings',
    Component: MyRecordingsRoute,
  },
  // A call of the history, shown in the logged-in home: matched by the
  // home route above, kept to build its links
  call: {
    name: 'call',
    path: new RegExp(`^/calls/(?<callId>${callIdPattern})$`),
    to: (callId: string) => `/calls/${callId.trim()}`,
    Component: HomeRoute,
  },
}

export type RouteName = keyof typeof routes
