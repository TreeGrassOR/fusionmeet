import { describe, expect, it, vi } from 'vitest'
import type { PaginatedResponse } from '../types'
import { fetchAllPages } from './fetchAllPages'

const page = (
  results: number[],
  next: string | null
): PaginatedResponse<number> => ({
  count: 5,
  next,
  previous: null,
  results,
})

describe('fetchAllPages', () => {
  it('follows the pages until there is no next one', async () => {
    const fetchPage = vi.fn(async (n: number) =>
      n === 1
        ? page([1, 2], '?page=2')
        : n === 2
          ? page([3, 4], '?page=3')
          : page([5], null)
    )

    await expect(fetchAllPages(fetchPage)).resolves.toEqual([1, 2, 3, 4, 5])
    expect(fetchPage.mock.calls).toEqual([[1], [2], [3]])
  })

  it('stops at the first page when it is the only one', async () => {
    const fetchPage = vi.fn(async () => page([1], null))

    await expect(fetchAllPages(fetchPage)).resolves.toEqual([1])
    expect(fetchPage).toHaveBeenCalledTimes(1)
  })

  it('never fetches more than the maximum number of pages', async () => {
    const fetchPage = vi.fn(async (n: number) => page([n], 'next'))

    await expect(fetchAllPages(fetchPage, 3)).resolves.toEqual([1, 2, 3])
    expect(fetchPage).toHaveBeenCalledTimes(3)
  })

  it('propagates the error of a page', async () => {
    const fetchPage = vi.fn(async (n: number) => {
      if (n === 2) throw new Error('boom')
      return page([n], 'next')
    })

    await expect(fetchAllPages(fetchPage)).rejects.toThrow('boom')
  })
})
