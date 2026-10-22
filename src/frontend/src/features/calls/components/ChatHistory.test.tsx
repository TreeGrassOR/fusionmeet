import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChatHistory } from './ChatHistory'
import type { CallDetailApi, CallMessageApi } from '../api/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      key === 'senderAt' ? `${values?.name}, ${values?.time}` : key,
    i18n: { language: 'ru' },
  }),
}))

const messages: CallMessageApi[] = [
  {
    id: 'm1',
    sender_key: 'k1',
    sender_name: 'Иван Петров',
    sender_is_me: false,
    text: 'Посмотрел договор',
    sent_at: new Date(2026, 9, 21, 19, 40, 0).toISOString(),
  },
  {
    // within a minute: grouped under the first one
    id: 'm2',
    sender_key: 'k1',
    sender_name: 'Иван Петров',
    sender_is_me: false,
    text: 'Сроки поставки нужно сдвинуть',
    sent_at: new Date(2026, 9, 21, 19, 40, 30).toISOString(),
  },
]

// the in-call chat (and LiveKit) is not needed for the links
vi.mock('@/features/chat/components/ChatMessageBody', () => ({
  ChatMessageBody: ({ message }: { message: string }) => <p>{message}</p>,
}))

vi.mock('../api/listCallMessages', () => ({
  useCallMessages: () => ({
    data: messages,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}))

const call = { id: 'c1', is_active: false } as CallDetailApi

describe('ChatHistory', () => {
  it('says who wrote each message, the grouped ones too', () => {
    const markup = renderToStaticMarkup(<ChatHistory call={call} />)
    const items = markup.match(/<li[\s\S]*?<\/li>/g) ?? []
    expect(items).toHaveLength(2)
    for (const item of items) {
      // a timestamp title would be the name of the list item
      expect(item).not.toMatch(/^<li[^>]*\stitle=/)
      expect(item).toContain('Иван Петров, 19:40')
    }
  })
})
