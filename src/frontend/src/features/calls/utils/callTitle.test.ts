import { describe, expect, it } from 'vitest'
import type { TFunction } from 'i18next'
import type { CallApi } from '../api/types'
import { formatCallTitle, getCallTitle, getCallTitleParts } from './callTitle'

const NOW = new Date(2026, 9, 22, 15, 30)

// Renders the keys of calls.json (ru) with their values
const t = ((key: string, values: Record<string, unknown>) => {
  switch (key) {
    case 'title.namesAndMore':
      return `${values.names} и ещё ${values.count}`
    case 'title.twoNames':
      return `${values.first} и ${values.second}`
    case 'title.onDate':
      return `Звонок ${values.date}`
    default:
      return key
  }
}) as unknown as TFunction<'calls'>

const call = (overrides: Partial<CallApi> = {}) => ({
  title: '',
  started_at: new Date(2026, 9, 22, 11, 3).toISOString(),
  participants: [],
  participants_count: 0,
  ...overrides,
})

const person = (name: string, is_me = false) => ({ name, is_me })

describe('call title', () => {
  it('keeps the title of the call', () => {
    expect(getCallTitle(call({ title: ' Планёрка ' }), t, 'ru', NOW)).toBe(
      'Планёрка'
    )
  })

  it('names the other participants by their first name', () => {
    const parts = getCallTitleParts(
      call({
        participants: [
          person('Александр Бирюков', true),
          person('Иван Петров'),
          person('Мария Соколова'),
          person('Олег Иванов'),
        ],
        participants_count: 4,
      })
    )
    expect(parts).toEqual({ kind: 'names', names: ['Иван', 'Мария'], more: 1 })
    expect(formatCallTitle(parts, t, 'ru', NOW)).toBe('Иван, Мария и ещё 1')
  })

  it('counts the participants beyond the listed ones', () => {
    // the list holds the first six participants, the user is not among them
    const listed = ['Иван', 'Мария', 'Олег', 'Анна', 'Пётр', 'Ольга'].map(
      (name) => person(name)
    )
    expect(
      getCallTitle(
        call({ participants: listed, participants_count: 9 }),
        t,
        'ru',
        NOW
      )
    ).toBe('Иван, Мария и ещё 7')
  })

  it('reads well with one or two other participants', () => {
    expect(
      getCallTitle(
        call({
          participants: [person('Я', true), person('Иван Петров')],
          participants_count: 2,
        }),
        t,
        'ru',
        NOW
      )
    ).toBe('Иван')
    expect(
      getCallTitle(
        call({
          participants: [person('Иван'), person('Мария')],
          participants_count: 2,
        }),
        t,
        'ru',
        NOW
      )
    ).toBe('Иван и Мария')
  })

  it('falls back to the date without other named participants', () => {
    expect(
      getCallTitle(
        call({ participants: [person('Я', true), person(' ')] }),
        t,
        'ru',
        NOW
      )
    ).toBe('Звонок 22 октября')
    expect(
      getCallTitle(
        call({ started_at: new Date(2025, 4, 3, 9).toISOString() }),
        t,
        'ru',
        NOW
      )
    ).toBe('Звонок 3 мая 2025 г.')
  })
})
