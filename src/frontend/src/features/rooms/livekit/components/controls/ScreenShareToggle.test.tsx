import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

const state = vi.hoisted(() => ({
  isPending: false,
  canShareScreen: true,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/features/rooms/livekit/hooks/useCanPublishTrack', () => ({
  useCanPublishTrack: () => state.canShareScreen,
}))

vi.mock('@/features/rooms/livekit/hooks/useScreenShareToggle', () => ({
  useScreenShareToggle: () => ({
    enabled: false,
    isPending: state.isPending,
    toggle: vi.fn(),
  }),
}))

const renderButton = async () => {
  const { ScreenShareToggle } = await import('./ScreenShareToggle')
  const markup = renderToStaticMarkup(<ScreenShareToggle />)
  const button = markup.match(/<button[^>]*>/)?.[0]
  expect(button).toBeDefined()
  return button as string
}

describe('ScreenShareToggle', () => {
  beforeEach(() => {
    state.isPending = false
    state.canShareScreen = true
  })

  it('stays focusable while the picker is open', async () => {
    // a disabled button drops the keyboard focus to <body>
    state.isPending = true
    const button = await renderButton()
    expect(button).not.toMatch(/\sdisabled(=|\s|>)/)
    expect(button).not.toMatch(/\sdata-disabled=/)
  })

  it('is disabled without the screen share permission', async () => {
    state.canShareScreen = false
    expect(await renderButton()).toMatch(/\sdisabled(=|\s|>)/)
  })
})
