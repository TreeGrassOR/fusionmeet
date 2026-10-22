import { describe, expect, it } from 'vitest'
import {
  formatCallDay,
  formatCallTimeLabel,
  formatDuration,
  msUntilNextDay,
} from './dates'

// Local times, so the labels read the same in any test timezone
const NOW = new Date(2026, 9, 22, 15, 30)

describe('formatCallTimeLabel', () => {
  const label = (date: Date, locale = 'ru') =>
    formatCallTimeLabel(date, NOW, locale, 'вчера')

  it('shows the time of a call today', () => {
    expect(label(new Date(2026, 9, 22, 11, 3))).toBe('11:03')
    expect(label(new Date(2026, 9, 22, 0, 0))).toBe('00:00')
  })

  it('says yesterday for the previous day, whatever the time', () => {
    expect(label(new Date(2026, 9, 21, 23, 59))).toBe('вчера')
    expect(label(new Date(2026, 9, 21, 0, 1))).toBe('вчера')
  })

  it('shows the day and month earlier this year', () => {
    expect(label(new Date(2026, 9, 20, 10))).toBe('20 окт.')
    expect(label(new Date(2026, 0, 1, 10))).toBe('1 янв.')
    expect(label(new Date(2026, 9, 20, 10), 'en')).toBe('Oct 20')
  })

  it('shows the full date of a previous year', () => {
    expect(label(new Date(2025, 9, 22, 10))).toBe('22.10.2025')
    expect(label(new Date(2025, 11, 31, 10))).toBe('31.12.2025')
  })

  it('handles yesterday across a month and a year', () => {
    const newYear = new Date(2026, 0, 1, 9)
    expect(
      formatCallTimeLabel(new Date(2025, 11, 31, 20), newYear, 'ru', 'вчера')
    ).toBe('вчера')
    expect(
      formatCallTimeLabel(new Date(2025, 11, 30, 20), newYear, 'ru', 'вчера')
    ).toBe('30.12.2025')
  })
})

describe('formatCallDay', () => {
  it('omits the current year', () => {
    expect(formatCallDay(new Date(2026, 9, 22), NOW, 'ru')).toBe('22 октября')
    expect(formatCallDay(new Date(2025, 2, 8), NOW, 'ru')).toBe(
      '8 марта 2025 г.'
    )
    expect(formatCallDay(new Date(2026, 9, 22), NOW, 'en')).toBe('October 22')
  })
})

describe('formatDuration', () => {
  const minutes = (value: number) => value * 60_000

  it('counts minutes, at least one', () => {
    expect(formatDuration(0, 'ru')).toBe('1 мин')
    expect(formatDuration(minutes(42), 'ru')).toBe('42 мин')
    expect(formatDuration(minutes(42) + 29_000, 'ru')).toBe('42 мин')
  })

  it('adds the hours of a long call', () => {
    expect(formatDuration(minutes(60), 'ru')).toBe('1 ч')
    expect(formatDuration(minutes(65), 'ru')).toBe('1 ч 5 мин')
    expect(formatDuration(minutes(125), 'en')).toBe('2 hr 5 min')
  })
})

describe('msUntilNextDay', () => {
  it('counts the time to the next local midnight', () => {
    expect(msUntilNextDay(new Date(2026, 9, 22, 23, 58))).toBe(2 * 60_000)
    expect(msUntilNextDay(new Date(2026, 9, 22, 0, 0))).toBe(
      new Date(2026, 9, 23).getTime() - new Date(2026, 9, 22).getTime()
    )
  })

  it('moves the labels to the next day once it is over', () => {
    const late = new Date(2026, 9, 22, 23, 58)
    const next = new Date(late.getTime() + msUntilNextDay(late) + 1000)
    // what was today is now yesterday
    expect(
      formatCallTimeLabel(new Date(2026, 9, 22, 20, 0), next, 'ru', 'вчера')
    ).toBe('вчера')
  })
})
