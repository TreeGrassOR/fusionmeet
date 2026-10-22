import { proxy } from 'valtio'

/**
 * What the call list keeps when it is mounted again (after the recordings
 * page, for instance).
 */
export const callsUiStore = proxy<{
  search: string
  // the call the user just opened from the list (not from a link)
  openedCallId: string | null
}>({
  search: '',
  openedCallId: null,
})
