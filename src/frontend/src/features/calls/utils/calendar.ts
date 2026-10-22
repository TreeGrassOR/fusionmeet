import { fileNameFromTitle } from './fileName'

export type CalendarEvent = {
  // unique and stable: "<room id>@<host>"
  uid: string
  title: string
  description: string
  // room URL, also the location
  url: string
  start: Date
  end: Date
}

// Lines longer than 75 octets are folded (RFC 5545 §3.1)
const MAX_LINE_OCTETS = 75

const pad = (value: number) => String(value).padStart(2, '0')

/**
 * UTC date-time of RFC 5545 ("20261022T090000Z"), also used by Google
 * Calendar links.
 */
export const formatUtcDateTime = (date: Date) =>
  `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}` +
  `T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`

/**
 * Escape a TEXT value (RFC 5545 §3.3.11).
 */
export const escapeIcsText = (value: string) =>
  value
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r\n|\r|\n/g, '\\n')

const encoder = new TextEncoder()
const octets = (value: string) => encoder.encode(value).length

/**
 * Fold a content line into lines of at most 75 octets, continued by a space,
 * without splitting a character.
 */
export const foldIcsLine = (line: string) => {
  const parts: string[] = []
  let current = ''
  let size = 0
  for (const char of line) {
    const charSize = octets(char)
    // continuation lines start with a space, which counts
    const limit = parts.length ? MAX_LINE_OCTETS - 1 : MAX_LINE_OCTETS
    if (size + charSize > limit) {
      parts.push(current)
      current = ''
      size = 0
    }
    current += char
    size += charSize
  }
  parts.push(current)
  return parts.join('\r\n ')
}

/**
 * An iCalendar file with one event, in UTC, that Yandex, Google, Outlook and
 * Apple calendars import.
 */
export const buildIcs = (
  event: CalendarEvent,
  { now = new Date(), productName }: { now?: Date; productName: string }
) => {
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    `PRODID:-//${productName.replace(/[/\r\n]/g, ' ')}//Calls//RU`,
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${event.uid}`,
    `DTSTAMP:${formatUtcDateTime(now)}`,
    `DTSTART:${formatUtcDateTime(event.start)}`,
    `DTEND:${formatUtcDateTime(event.end)}`,
    `SUMMARY:${escapeIcsText(event.title)}`,
    `DESCRIPTION:${escapeIcsText(event.description)}`,
    `LOCATION:${escapeIcsText(event.url)}`,
    `URL:${event.url}`,
    'END:VEVENT',
    'END:VCALENDAR',
  ]
  return lines.map(foldIcsLine).join('\r\n') + '\r\n'
}

/**
 * Link that opens a prefilled event in Google Calendar.
 */
export const googleCalendarUrl = (event: CalendarEvent) => {
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: event.title,
    dates: `${formatUtcDateTime(event.start)}/${formatUtcDateTime(event.end)}`,
    details: event.description,
    location: event.url,
  })
  return `https://calendar.google.com/calendar/render?${params}`
}

/**
 * File name of the downloaded event, from its title.
 */
export const icsFileName = (title: string) =>
  `${fileNameFromTitle(title) || 'meeting'}.ics`
