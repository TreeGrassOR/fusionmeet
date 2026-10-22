import { describe, expect, it } from 'vitest'
import type { CallMessageApi } from '../api/types'
import { groupMessages } from './chatGrouping'

const at = (hours: number, minutes: number, seconds = 0, day = 22) =>
  new Date(2026, 9, day, hours, minutes, seconds).toISOString()

let counter = 0
const message = (
  sender_name: string,
  sent_at: string,
  sender_is_me = false
): CallMessageApi => ({
  id: `m${++counter}`,
  sender_name,
  sender_is_me,
  text: 'текст',
  sent_at,
})

const headers = (messages: CallMessageApi[]) =>
  groupMessages(messages).map((row) => row.showHeader)

describe('groupMessages', () => {
  it('groups the messages of one sender within a minute', () => {
    expect(
      headers([
        message('Иван', at(10, 0, 0)),
        message('Иван', at(10, 0, 30)),
        message('Иван', at(10, 1, 20)),
        message('Иван', at(10, 2, 20)),
      ])
    ).toEqual([true, false, false, true])
  })

  it('starts a group when the sender changes', () => {
    expect(
      headers([
        message('Иван', at(10, 0)),
        message('Мария', at(10, 0, 10)),
        message('Иван', at(10, 0, 20)),
      ])
    ).toEqual([true, true, true])
  })

  it('tells the user apart from a namesake', () => {
    expect(
      headers([message('Иван', at(10, 0)), message('Иван', at(10, 0, 5), true)])
    ).toEqual([true, true])
  })

  it('marks a new day', () => {
    const rows = groupMessages([
      message('Иван', at(23, 59, 50, 21)),
      message('Иван', at(0, 0, 10, 22)),
    ])
    expect(rows.map((row) => row.isNewDay)).toEqual([false, true])
    expect(rows.map((row) => row.showHeader)).toEqual([true, true])
  })

  it('keeps the messages and their times', () => {
    const [row] = groupMessages([message('Иван', at(10, 0))])
    expect(row).toMatchObject({ sender_name: 'Иван', text: 'текст' })
    expect(row.timestamp).toBe(new Date(2026, 9, 22, 10, 0).getTime())
  })
})
