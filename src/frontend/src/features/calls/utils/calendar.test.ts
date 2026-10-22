import { describe, expect, it } from 'vitest'
import {
  buildIcs,
  escapeIcsText,
  foldIcsLine,
  formatUtcDateTime,
  googleCalendarUrl,
  icsFileName,
  type CalendarEvent,
} from './calendar'

const EVENT: CalendarEvent = {
  uid: '8f1c2c7e-1111-4a2b-9c3d-123456789abc@med-question.online',
  title: 'Планёрка; отдел, продажи',
  description: 'Ссылка на звонок:\nhttps://med-question.online/abc-defg-hij',
  url: 'https://med-question.online/abc-defg-hij',
  start: new Date(Date.UTC(2026, 9, 23, 7, 0)),
  end: new Date(Date.UTC(2026, 9, 23, 7, 30)),
}

const NOW = new Date(Date.UTC(2026, 9, 22, 12, 34, 56))

const unfold = (ics: string) => ics.replace(/\r\n /g, '')

describe('iCalendar file', () => {
  it('writes UTC date-times', () => {
    expect(formatUtcDateTime(EVENT.start)).toBe('20261023T070000Z')
    expect(formatUtcDateTime(NOW)).toBe('20261022T123456Z')
  })

  it('escapes text values', () => {
    expect(escapeIcsText('a\\b;c,d\ne\r\nf')).toBe('a\\\\b\\;c\\,d\\ne\\nf')
  })

  it('folds long lines without splitting characters', () => {
    const line = `SUMMARY:${'Ж'.repeat(60)}`
    const folded = foldIcsLine(line)
    const parts = folded.split('\r\n')
    const encoder = new TextEncoder()
    expect(parts.length).toBeGreaterThan(1)
    for (const part of parts) {
      expect(encoder.encode(part).length).toBeLessThanOrEqual(75)
    }
    expect(parts.slice(1).every((part) => part.startsWith(' '))).toBe(true)
    expect(folded.replace(/\r\n /g, '')).toBe(line)
    expect(foldIcsLine('SHORT:line')).toBe('SHORT:line')
  })

  it('describes the event', () => {
    const ics = buildIcs(EVENT, { now: NOW, productName: 'Fusion Meet' })
    expect(ics.endsWith('\r\n')).toBe(true)
    expect(ics.split('\r\n').every((line) => !line.includes('\n'))).toBe(true)
    const lines = unfold(ics).trimEnd().split('\r\n')
    expect(lines).toEqual([
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//Fusion Meet//Calls//RU',
      'CALSCALE:GREGORIAN',
      'METHOD:PUBLISH',
      'BEGIN:VEVENT',
      `UID:${EVENT.uid}`,
      'DTSTAMP:20261022T123456Z',
      'DTSTART:20261023T070000Z',
      'DTEND:20261023T073000Z',
      'SUMMARY:Планёрка\\; отдел\\, продажи',
      'DESCRIPTION:Ссылка на звонок:\\nhttps://med-question.online/abc-defg-hij',
      'LOCATION:https://med-question.online/abc-defg-hij',
      'URL:https://med-question.online/abc-defg-hij',
      'END:VEVENT',
      'END:VCALENDAR',
    ])
  })

  it('names the file after the title', () => {
    expect(icsFileName('Планёрка: отдел/продажи')).toBe(
      'Планёрка отдел продажи.ics'
    )
    expect(icsFileName('  ')).toBe('meeting.ics')
  })
})

describe('Google Calendar link', () => {
  it('prefills the event', () => {
    const url = new URL(googleCalendarUrl(EVENT))
    expect(url.origin + url.pathname).toBe(
      'https://calendar.google.com/calendar/render'
    )
    expect(Object.fromEntries(url.searchParams)).toEqual({
      action: 'TEMPLATE',
      text: EVENT.title,
      dates: '20261023T070000Z/20261023T073000Z',
      details: EVENT.description,
      location: EVENT.url,
    })
  })
})
