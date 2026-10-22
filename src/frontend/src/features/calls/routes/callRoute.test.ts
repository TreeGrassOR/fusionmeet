import { describe, expect, it } from 'vitest'
import { routes } from '@/routes'

const CALL_ID = '8f1c2c7e-1111-4a2b-9c3d-123456789abc'

describe('call route', () => {
  const callPath = routes.call.path as RegExp

  it('matches /calls/<uuid> and names the id', () => {
    expect(callPath.exec(`/calls/${CALL_ID}`)?.groups?.callId).toBe(CALL_ID)
    expect(routes.call.to?.(CALL_ID)).toBe(`/calls/${CALL_ID}`)
  })

  it('refuses other paths', () => {
    expect(callPath.test('/calls/')).toBe(false)
    expect(callPath.test('/calls/abc-defg-hij')).toBe(false)
    expect(callPath.test(`/calls/${CALL_ID}/x`)).toBe(false)
  })

  it('is not captured by the room route', () => {
    expect((routes.room.path as RegExp).test(`/calls/${CALL_ID}`)).toBe(false)
  })
})
