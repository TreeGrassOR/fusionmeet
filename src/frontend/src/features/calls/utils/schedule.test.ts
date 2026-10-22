import { describe, expect, it } from 'vitest'
import { formatTimeZone, isStartInPast } from './schedule'

describe('isStartInPast', () => {
  const now = new Date(2026, 9, 22, 15, 30)

  it('refuses a start that is over', () => {
    // a mistyped date
    expect(isStartInPast(new Date(2026, 9, 1, 9, 0), now)).toBe(true)
    expect(isStartInPast(new Date(2026, 9, 22, 15, 20), now)).toBe(true)
  })

  it('accepts a start in the future or a few minutes ago', () => {
    expect(isStartInPast(new Date(2026, 9, 22, 16, 0), now)).toBe(false)
    expect(isStartInPast(new Date(2026, 9, 22, 15, 27), now)).toBe(false)
  })
})

describe('formatTimeZone', () => {
  const october = new Date('2026-10-22T09:00:00Z')

  it('says the offset of the zone from UTC', () => {
    expect(formatTimeZone(october, 'Europe/Moscow')).toBe('UTC+3')
    expect(formatTimeZone(october, 'America/New_York')).toBe('UTC−4')
  })

  it('writes the minutes of an offset', () => {
    expect(formatTimeZone(october, 'Asia/Kolkata')).toBe('UTC+5:30')
  })

  it('follows daylight saving time', () => {
    const january = new Date('2026-01-15T09:00:00Z')
    expect(formatTimeZone(january, 'America/New_York')).toBe('UTC−5')
  })

  it('says UTC alone for UTC', () => {
    expect(formatTimeZone(october, 'UTC')).toBe('UTC')
    expect(formatTimeZone(new Date('2026-01-15'), 'Europe/London')).toBe('UTC')
  })

  it('uses the zone of the browser by default', () => {
    const { timeZone } = Intl.DateTimeFormat().resolvedOptions()
    expect(formatTimeZone(october)).toBe(formatTimeZone(october, timeZone))
  })
})
