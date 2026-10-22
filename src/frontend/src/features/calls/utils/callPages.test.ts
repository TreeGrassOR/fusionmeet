import { describe, expect, it } from 'vitest'
import type { CallApi, PaginatedResponse } from '../api/types'
import {
  flattenCallPages,
  markCallReadInPages,
  type CallPages,
} from './callPages'

const call = (id: string, unread_count = 0) =>
  ({ id, unread_count, title: id }) as CallApi

const page = (...results: CallApi[]): PaginatedResponse<CallApi> => ({
  count: 100,
  next: null,
  previous: null,
  results,
})

describe('flattenCallPages', () => {
  it('lists the calls of the pages in order', () => {
    expect(
      flattenCallPages([page(call('a'), call('b')), page(call('c'))]).map(
        (c) => c.id
      )
    ).toEqual(['a', 'b', 'c'])
    expect(flattenCallPages(undefined)).toEqual([])
  })

  it('shows once a call that moved to the next page', () => {
    // a new call came on top after page 1 was loaded: page 2 starts with
    // the last call of page 1
    const calls = flattenCallPages([
      page(call('a'), call('b'), call('c')),
      page(call('c'), call('d')),
    ])
    expect(calls.map((c) => c.id)).toEqual(['a', 'b', 'c', 'd'])
  })
})

describe('markCallReadInPages', () => {
  const data: CallPages = {
    pages: [page(call('a', 2), call('b')), page(call('c', 1))],
    pageParams: [1, 2],
  }

  it('clears the unread count of the call only', () => {
    const marked = markCallReadInPages(data, 'c')
    expect(
      marked?.pages.flatMap((p) => p.results.map((c) => c.unread_count))
    ).toEqual([2, 0, 0])
    // the untouched page is kept as it was
    expect(marked?.pages[0]).toBe(data.pages[0])
  })

  it('changes nothing when the call is read or not loaded', () => {
    expect(markCallReadInPages(data, 'b')).toBe(data)
    expect(markCallReadInPages(data, 'zzz')).toBe(data)
    expect(markCallReadInPages(undefined, 'a')).toBeUndefined()
  })
})
