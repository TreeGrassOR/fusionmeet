import { useEffect, useState } from 'react'
import { isSameDay, msUntilNextDay } from '../utils/dates'

// fire just after midnight, timers may run a little early
const AFTER_MIDNIGHT_MS = 1000

/**
 * The current date, renewed when the day changes: labels such as «вчера»
 * are relative to it. Timers of hidden tabs or asleep computers may be late,
 * so it is also checked when the page is shown again.
 */
export const useToday = () => {
  const [today, setToday] = useState(() => new Date())

  useEffect(() => {
    const renew = () => {
      const now = new Date()
      if (!isSameDay(now, today)) setToday(now)
    }
    const timeout = setTimeout(
      () => setToday(new Date()),
      msUntilNextDay(today) + AFTER_MIDNIGHT_MS
    )
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') renew()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      clearTimeout(timeout)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [today])

  return today
}
