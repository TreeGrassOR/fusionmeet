import { useEffect } from 'react'
import { subscribe } from 'valtio'
import { useParams } from 'wouter'
import { useRoomContext } from '@livekit/components-react'
import { keys } from '@/api/queryKeys'
import { queryClient } from '@/api/queryClient'
import { useConfig } from '@/api/useConfig'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'
import { chatStore } from '@/stores/chat'
import { postChatMessage } from '../api/postChatMessage'
import {
  selectRowsToPersist,
  toChatMessagePayload,
  withRetries,
} from '../utils/chatPersistence'

// Wait no longer for the room sid: the backend finds the call without it
const ROOM_SID_TIMEOUT_MS = 5000

/**
 * Store in the call history the chat messages the local participant sends
 * (each client posts its own, with its LiveKit token). Mounted next to the
 * ChatProvider; renders nothing and never blocks the chat.
 */
export const ChatPersistence = () => {
  const { data: config } = useConfig()
  const isEnabled = !!config?.calls?.enabled
  const room = useRoomContext()
  const { roomId } = useParams()

  useEffect(() => {
    if (!isEnabled) return

    // messages already there belong to a previous room: never post them
    const handled = new Set(chatStore.rows.map((row) => row.id))

    const getRoomSid = () =>
      Promise.race([
        room.getSid(),
        new Promise<string>((resolve) =>
          setTimeout(() => resolve(''), ROOM_SID_TIMEOUT_MS)
        ),
      ]).catch(() => '')

    const persist = () => {
      for (const row of selectRowsToPersist(chatStore.rows, handled)) {
        handled.add(row.id)
        const name = room.localParticipant.name ?? null
        void getRoomSid()
          .then((roomSid) =>
            withRetries(() => {
              // read at each attempt: the room data holds a fresh token
              // once it is fetched again
              const roomData = queryClient.getQueryData<ApiRoom>([
                keys.room,
                roomId,
              ])
              const token = roomData?.livekit?.token
              if (!roomData?.id || !token) {
                return Promise.reject(new Error('No LiveKit token'))
              }
              return postChatMessage({
                roomId: roomData.id,
                token,
                message: toChatMessagePayload(row, { roomSid, name }),
              })
            })
          )
          .catch((error) => {
            console.warn('[ChatPersistence] message not stored', error)
          })
      }
    }

    persist()
    return subscribe(chatStore.rows, persist)
  }, [isEnabled, room, roomId])

  return null
}
