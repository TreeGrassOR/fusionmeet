export const isSameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() &&
  a.getMonth() === b.getMonth() &&
  a.getDate() === b.getDate()

const dayBefore = (date: Date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate() - 1)

/**
 * Milliseconds from `date` to the next local midnight (daylight saving time
 * included), when the labels below change.
 */
export const msUntilNextDay = (date: Date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1).getTime() -
  date.getTime()

/**
 * Time label of a call in the list, in local time: the time today
 * ("11:03"), `yesterday` ("вчера"), the day and month this year
 * ("22 окт."), the full date before ("22.10.2025").
 */
export const formatCallTimeLabel = (
  date: Date,
  now: Date,
  locale: string,
  yesterday: string
): string => {
  if (isSameDay(date, now)) {
    return new Intl.DateTimeFormat(locale, {
      hour: '2-digit',
      minute: '2-digit',
    }).format(date)
  }
  if (isSameDay(date, dayBefore(now))) return yesterday
  if (date.getFullYear() === now.getFullYear()) {
    return new Intl.DateTimeFormat(locale, {
      day: 'numeric',
      month: 'short',
    }).format(date)
  }
  return new Intl.DateTimeFormat(locale, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(date)
}

/**
 * Day of a call in titles ("22 октября"), with the year when it is not the
 * current one.
 */
export const formatCallDay = (date: Date, now: Date, locale: string) =>
  new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'long',
    ...(date.getFullYear() !== now.getFullYear() && { year: 'numeric' }),
  }).format(date)

/**
 * Duration of a call in minutes, with the hours when longer ("1 ч 5 мин").
 * A call shorter than a minute counts as one minute.
 */
export const formatDuration = (milliseconds: number, locale: string) => {
  const totalMinutes = Math.max(1, Math.round(milliseconds / 60_000))
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  const unit = (value: number, name: 'hour' | 'minute') =>
    new Intl.NumberFormat(locale, {
      style: 'unit',
      unit: name,
      unitDisplay: 'short',
    }).format(value)
  if (!hours) return unit(minutes, 'minute')
  if (!minutes) return unit(hours, 'hour')
  return `${unit(hours, 'hour')} ${unit(minutes, 'minute')}`
}
