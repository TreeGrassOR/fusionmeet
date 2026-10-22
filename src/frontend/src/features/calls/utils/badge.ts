const MAX_BADGE_COUNT = 99

/**
 * Unread count of a badge: "99+" beyond 99.
 */
export const formatBadgeCount = (count: number) =>
  count > MAX_BADGE_COUNT ? `${MAX_BADGE_COUNT}+` : String(count)
