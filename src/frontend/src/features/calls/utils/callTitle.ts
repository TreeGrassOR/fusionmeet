import type { TFunction } from 'i18next'
import type { CallApi } from '../api/types'
import { formatCallDay } from './dates'

// Names shown in a title built from the participants
const TITLE_NAMES = 2

export type CallTitleParts =
  | { kind: 'title'; title: string }
  | { kind: 'names'; names: string[]; more: number }
  | { kind: 'date'; date: Date }

type TitledCall = Pick<
  CallApi,
  'title' | 'participants' | 'participants_count' | 'started_at'
> &
  Partial<Pick<CallApi, 'participated'>>

const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? ''

/**
 * What the title of a call is made of: its own title, else the first names
 * of the other participants ("Иван, Мария и ещё 2"), else its date
 * ("Звонок 22 октября").
 */
export const getCallTitleParts = (call: TitledCall): CallTitleParts => {
  const title = call.title.trim()
  if (title) return { kind: 'title', title }

  const others = call.participants.filter((p) => !p.is_me)
  const names = others
    .map((p) => firstName(p.name))
    .filter(Boolean)
    .slice(0, TITLE_NAMES)
  if (names.length) {
    // the list holds the first participants only, the count all of them,
    // the user too when they took part (listed or not)
    const isMeCounted =
      call.participated ?? call.participants.some((p) => p.is_me)
    const othersCount = Math.max(
      others.length,
      call.participants_count - (isMeCounted ? 1 : 0)
    )
    return { kind: 'names', names, more: othersCount - names.length }
  }

  return { kind: 'date', date: new Date(call.started_at) }
}

export const formatCallTitle = (
  parts: CallTitleParts,
  t: TFunction<'calls'>,
  locale: string,
  now: Date = new Date()
): string => {
  switch (parts.kind) {
    case 'title':
      return parts.title
    case 'names': {
      const [first, second] = parts.names
      if (parts.more > 0) {
        return t('title.namesAndMore', {
          names: parts.names.join(', '),
          count: parts.more,
        })
      }
      return second ? t('title.twoNames', { first, second }) : first
    }
    case 'date':
      return t('title.onDate', {
        date: formatCallDay(parts.date, now, locale),
      })
  }
}

export const getCallTitle = (
  call: TitledCall,
  t: TFunction<'calls'>,
  locale: string,
  now?: Date
) => formatCallTitle(getCallTitleParts(call), t, locale, now)
