import { describe, expect, it } from 'vitest'
import { isOpenedFromList, openedFromListState } from './backToList'

describe('back to the list', () => {
  it('marks the calls opened from the home', () => {
    expect(isOpenedFromList(openedFromListState('/'))).toBe(true)
  })

  it('does not mark a call opened from another call', () => {
    expect(isOpenedFromList(openedFromListState('/calls/abc'))).toBe(false)
  })

  it('ignores other history states', () => {
    expect(isOpenedFromList(null)).toBe(false)
    expect(isOpenedFromList(undefined)).toBe(false)
    expect(isOpenedFromList('x')).toBe(false)
    expect(isOpenedFromList({ create: true })).toBe(false)
  })
})
