// History state of a call opened from the list of the home ("/")
type OpenedFromListState = { openedFromCallsList: true }

/**
 * The state to push with a call opened from the list: the back arrow of a
 * narrow screen then goes back in the history, so that the back button of
 * the phone does not open the call again.
 */
export const openedFromListState = (
  pathname: string
): OpenedFromListState | null =>
  pathname === '/' ? { openedFromCallsList: true } : null

/**
 * Whether the previous history entry is the list (see above). Otherwise
 * (a link to the call, a reload) the back arrow replaces the call with it.
 */
export const isOpenedFromList = (state: unknown) =>
  typeof state === 'object' &&
  state !== null &&
  (state as Partial<OpenedFromListState>).openedFromCallsList === true
