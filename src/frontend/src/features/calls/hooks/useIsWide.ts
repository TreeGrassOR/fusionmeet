import { useMediaQuery } from '@/features/rooms/livekit/hooks/useMediaQuery'

// keep in sync with the md breakpoint of panda.config.ts
const WIDE_QUERY = '(min-width: 48em)'

/**
 * Whether the home shows its three columns (the rail, the list, the call),
 * or one at a time.
 */
export const useIsWide = () => useMediaQuery(WIDE_QUERY)
