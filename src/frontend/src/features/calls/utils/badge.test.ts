import { describe, expect, it } from 'vitest'
import { formatBadgeCount } from './badge'

describe('formatBadgeCount', () => {
  it('caps the count at 99', () => {
    expect(formatBadgeCount(1)).toBe('1')
    expect(formatBadgeCount(99)).toBe('99')
    expect(formatBadgeCount(100)).toBe('99+')
  })
})
