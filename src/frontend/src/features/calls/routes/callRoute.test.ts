import { describe, expect, it } from 'vitest'
import { routes } from '@/routes'

const CALL_ID = '8f1c2c7e-1111-4a2b-9c3d-123456789abc'

describe('call route', () => {
  const callPath = routes.call.path as RegExp
  const homePath = routes.home.path as RegExp

  it('matches /calls/<uuid> and names the id', () => {
    expect(callPath.exec(`/calls/${CALL_ID}`)?.groups?.callId).toBe(CALL_ID)
    expect(routes.call.to?.(CALL_ID)).toBe(`/calls/${CALL_ID}`)
  })

  it('refuses other paths', () => {
    for (const path of [
      '/calls/',
      '/calls/abc-defg-hij',
      `/calls/${CALL_ID}/x`,
    ]) {
      expect(callPath.test(path)).toBe(false)
      expect(homePath.test(path)).toBe(false)
    }
  })

  it('is served by the home route, so the calls stay mounted', () => {
    // the routes are tried in order: the home route takes the calls
    expect(Object.keys(routes).indexOf('home')).toBe(0)
    expect(homePath.exec(`/calls/${CALL_ID}`)?.groups?.callId).toBe(CALL_ID)
    expect(homePath.test('/')).toBe(true)
    expect(homePath.exec('/')?.groups?.callId).toBeUndefined()
    expect(routes.home.to?.()).toBe('/')
  })

  it('is not captured by the room route', () => {
    expect((routes.room.path as RegExp).test(`/calls/${CALL_ID}`)).toBe(false)
  })

  it('leaves the other pages to their routes', () => {
    for (const path of ['/abc-defg-hij', '/my-recordings', '/feedback']) {
      expect(homePath.test(path)).toBe(false)
    }
  })
})
