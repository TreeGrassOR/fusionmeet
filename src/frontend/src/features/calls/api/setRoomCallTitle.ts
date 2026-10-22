import { fetchApi } from '@/api/fetchApi'

/**
 * Title of the next calls of a room (room owners and administrators), kept
 * out of the room name, which gives the room its URL. "" removes it.
 */
export const setRoomCallTitle = (roomId: string, title: string) =>
  fetchApi<{ title: string }>(`rooms/${roomId}/call-title/`, {
    method: 'PUT',
    body: JSON.stringify({ title }),
  })
