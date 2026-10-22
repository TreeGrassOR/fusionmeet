import { proxy } from 'valtio'

/**
 * What the call list keeps when the page changes between the home and a
 * call (two routes, so the list is mounted again).
 */
export const callsUiStore = proxy({
  search: '',
})
