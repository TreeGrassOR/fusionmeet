import { fetchApi } from '@/api/fetchApi'
import { getLiveKitAuthHeaders } from '@/features/rooms/utils/getLiveKitAuthHeaders'

export type ChatMessagePayload = {
  // id of the LiveKit text stream, the same for every participant
  id: string
  text: string
  // epoch milliseconds
  sent_at: number
  room_sid: string | null
  name: string | null
}

/**
 * Store a chat message the local participant sent, with its LiveKit token:
 * 201 when stored, 200 when it already was.
 */
export const postChatMessage = ({
  roomId,
  token,
  message,
}: {
  roomId: string
  token: string
  message: ChatMessagePayload
}) =>
  fetchApi(`rooms/${roomId}/chat-messages/`, {
    method: 'POST',
    headers: getLiveKitAuthHeaders(token),
    body: JSON.stringify(message),
  })
