import type { PaginatedResponse } from '../types'

// Largest page the backend accepts (core.api.viewsets.Pagination).
export const MAX_PAGE_SIZE = 100

// Safety net against a pagination loop: 50 pages of 100 items.
const MAX_PAGES = 50

/**
 * Fetch every page of a paginated list endpoint, following `next` until the
 * last page.
 */
export const fetchAllPages = async <T>(
  fetchPage: (page: number) => Promise<PaginatedResponse<T>>,
  maxPages: number = MAX_PAGES
): Promise<T[]> => {
  const results: T[] = []
  for (let page = 1; page <= maxPages; page++) {
    const data = await fetchPage(page)
    results.push(...(data?.results ?? []))
    if (!data?.next) break
  }
  return results
}
