import type { ApiConfig } from '@/api/useConfig'

/**
 * Key of the notice above the in-call chat (rooms.json, `chat.*`): the
 * messages are kept in the call history only when it is enabled
 * (ChatPersistence), and room owners and administrators read it too unless
 * CALLS_VISIBLE_TO_ROOM_ADMINS is off.
 */
export const chatDisclaimerKey = (calls: ApiConfig['calls']) => {
  if (!calls?.enabled) return 'disclaimer' as const
  return calls.visible_to_room_admins === false
    ? ('disclaimerSaved' as const)
    : ('disclaimerSavedWithAdmins' as const)
}
