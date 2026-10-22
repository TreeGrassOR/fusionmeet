import { describe, expect, it } from 'vitest'
import { AVATAR_COLORS, avatarColor, hashString, pickIndex } from './colors'

describe('colours from ids', () => {
  it('are stable', () => {
    expect(hashString('8f1c2c7e-1111-4a2b-9c3d-123456789abc')).toBe(
      hashString('8f1c2c7e-1111-4a2b-9c3d-123456789abc')
    )
    expect(avatarColor('room-a')).toBe(avatarColor('room-a'))
  })

  it('stay in the palette and spread over it', () => {
    const picked = new Set<number>()
    for (let i = 0; i < 200; i++) {
      const index = pickIndex(`room-${i}`, AVATAR_COLORS.length)
      expect(index).toBeGreaterThanOrEqual(0)
      expect(index).toBeLessThan(AVATAR_COLORS.length)
      picked.add(index)
    }
    expect(picked.size).toBe(AVATAR_COLORS.length)
  })
})
