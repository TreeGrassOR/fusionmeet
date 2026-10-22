import { afterEach, describe, expect, it, vi } from 'vitest'
import { resolveJoinWarmupKinds } from './joinWarmupKinds'

const stubPermissions = (states: Record<string, PermissionState>) => {
  const query = vi.fn(async ({ name }: { name: string }) => ({
    state: states[name] ?? 'prompt',
  }))
  vi.stubGlobal('navigator', { permissions: { query } })
  return query
}

describe('resolveJoinWarmupKinds', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests nothing when both devices are off', async () => {
    const query = stubPermissions({})
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: false, videoEnabled: false })
    ).resolves.toEqual({ audio: false, video: false })
    expect(query).not.toHaveBeenCalled()
  })

  it('leaves the camera out when it is off', async () => {
    const query = stubPermissions({})
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: true, videoEnabled: false })
    ).resolves.toEqual({ audio: true, video: false })
    expect(query).toHaveBeenCalledTimes(1)
    expect(query).toHaveBeenCalledWith({ name: 'microphone' })
  })

  it('requests both kinds while they still need a permission', async () => {
    stubPermissions({ camera: 'prompt', microphone: 'prompt' })
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: true, videoEnabled: true })
    ).resolves.toEqual({ audio: true, video: true })
  })

  it('skips a kind the browser already allows', async () => {
    stubPermissions({ camera: 'granted', microphone: 'prompt' })
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: true, videoEnabled: true })
    ).resolves.toEqual({ audio: true, video: false })
  })

  it('still requests a denied kind, so the denial is reported', async () => {
    stubPermissions({ camera: 'denied', microphone: 'granted' })
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: true, videoEnabled: true })
    ).resolves.toEqual({ audio: false, video: true })
  })

  it('warms up enabled kinds without the Permissions API', async () => {
    vi.stubGlobal('navigator', {})
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: true, videoEnabled: true })
    ).resolves.toEqual({ audio: true, video: true })
  })

  it('warms up a kind the browser cannot query', async () => {
    vi.stubGlobal('navigator', {
      permissions: {
        query: vi.fn(async ({ name }: { name: string }) => {
          if (name === 'camera') throw new TypeError('unsupported name')
          return { state: 'granted' }
        }),
      },
    })
    await expect(
      resolveJoinWarmupKinds({ audioEnabled: true, videoEnabled: true })
    ).resolves.toEqual({ audio: false, video: true })
  })
})
