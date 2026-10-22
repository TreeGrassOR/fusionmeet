import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/ApiError'
import type { ChatRow } from '@/stores/chat'
import {
  isRetryableError,
  selectRowsToPersist,
  toChatMessagePayload,
  withRetries,
} from './chatPersistence'

const row = (overrides: Partial<ChatRow> = {}): ChatRow => ({
  id: 'id-1',
  identity: 'me',
  message: 'Привет',
  timestamp: 1_760_000_000_000,
  hideMetadata: false,
  isLocal: true,
  ...overrides,
})

describe('selectRowsToPersist', () => {
  it('keeps the local messages not handled yet', () => {
    const rows = [
      row({ id: 'a' }),
      row({ id: 'b', isLocal: false, identity: 'other' }),
      row({ id: 'c' }),
      row({ id: 'd' }),
    ]
    expect(selectRowsToPersist(rows, new Set(['c'])).map((r) => r.id)).toEqual([
      'a',
      'd',
    ])
  })

  it('selects a message once', () => {
    const handled = new Set<string>()
    const rows = [row({ id: 'a' })]
    for (const selected of selectRowsToPersist(rows, handled)) {
      handled.add(selected.id)
    }
    rows.push(row({ id: 'b' }))
    expect(selectRowsToPersist(rows, handled).map((r) => r.id)).toEqual(['b'])
  })

  it('leaves out what the backend refuses', () => {
    const rows = [
      row({ id: 'blank', message: ' \n ' }),
      row({ id: 'long', message: 'x'.repeat(2001) }),
      row({ id: 'max', message: 'x'.repeat(2000) }),
    ]
    expect(selectRowsToPersist(rows, new Set()).map((r) => r.id)).toEqual([
      'max',
    ])
  })
})

describe('toChatMessagePayload', () => {
  it('builds the body of the API', () => {
    expect(
      toChatMessagePayload(row({ timestamp: 1_760_000_000_000.4 }), {
        roomSid: 'RM_abc',
        name: 'Иван',
      })
    ).toEqual({
      id: 'id-1',
      text: 'Привет',
      sent_at: 1_760_000_000_000,
      room_sid: 'RM_abc',
      name: 'Иван',
    })
    expect(
      toChatMessagePayload(row(), { roomSid: '', name: '' })
    ).toMatchObject({ room_sid: null, name: null })
  })
})

describe('withRetries', () => {
  it('retries network and server errors three times, waiting longer', async () => {
    const sleep = vi.fn(() => Promise.resolve())
    const request = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new ApiError(502, undefined))
      .mockRejectedValueOnce(new ApiError(429, undefined))
      .mockResolvedValueOnce('stored')
    await expect(withRetries(request, { sleep })).resolves.toBe('stored')
    expect(request).toHaveBeenCalledTimes(4)
    expect(sleep.mock.calls).toEqual([[1000], [2000], [4000]])
  })

  it('gives up after the third retry', async () => {
    const sleep = vi.fn(() => Promise.resolve())
    const error = new ApiError(503, undefined)
    const request = vi.fn(() => Promise.reject(error))
    await expect(withRetries(request, { sleep })).rejects.toBe(error)
    expect(request).toHaveBeenCalledTimes(4)
  })

  it('does not retry a refused message', async () => {
    const sleep = vi.fn(() => Promise.resolve())
    for (const status of [400, 401, 403, 404, 409]) {
      const request = vi.fn(() => Promise.reject(new ApiError(status, {})))
      await expect(withRetries(request, { sleep })).rejects.toBeInstanceOf(
        ApiError
      )
      expect(request).toHaveBeenCalledTimes(1)
    }
    expect(sleep).not.toHaveBeenCalled()
    expect(isRetryableError(new ApiError(500, undefined))).toBe(true)
  })
})
