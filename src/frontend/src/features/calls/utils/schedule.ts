// A scheduled call may start a few minutes ago (the form stayed open)
export const PAST_START_GRACE_MS = 5 * 60_000

/**
 * Whether a call would be scheduled in the past (a mistyped date).
 */
export const isStartInPast = (start: Date, now: Date = new Date()) =>
  start.getTime() < now.getTime() - PAST_START_GRACE_MS

// "GMT+03:00" → 180, "GMT" → 0
const parseOffsetMinutes = (value: string | undefined) => {
  if (value === 'GMT' || value === 'UTC') return 0
  const match = value?.match(/([+\-−])(\d{1,2}):?(\d{2})?$/)
  if (!match) return undefined
  const minutes = Number(match[2]) * 60 + Number(match[3] ?? 0)
  return match[1] === '+' ? minutes : -minutes
}

// minutes east of UTC of a time zone at a date
const offsetMinutes = (date: Date, timeZone: string | undefined) => {
  try {
    const value = new Intl.DateTimeFormat('en', {
      timeZone,
      timeZoneName: 'longOffset',
    })
      .formatToParts(date)
      .find((part) => part.type === 'timeZoneName')?.value
    const minutes = parseOffsetMinutes(value)
    if (minutes !== undefined) return minutes
  } catch {
    // an old browser, or an unknown zone
  }
  return -date.getTimezoneOffset()
}

/**
 * The time zone a date is shown in, for the schedule form and its result:
 * "UTC+3", "UTC−4", "UTC+5:30", "UTC". The browser's zone by default. Only
 * the offset: browsers translate few zone names (a Russian Chrome says
 * «Moscow»), the offset reads the same in every language.
 */
export const formatTimeZone = (date: Date, timeZone?: string) => {
  const minutes = offsetMinutes(date, timeZone)
  if (!minutes) return 'UTC'
  const hours = Math.floor(Math.abs(minutes) / 60)
  const rest = Math.abs(minutes) % 60
  return `UTC${minutes > 0 ? '+' : '−'}${hours}${rest ? `:${String(rest).padStart(2, '0')}` : ''}`
}
