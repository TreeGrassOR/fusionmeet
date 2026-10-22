import { useEffect, useState } from 'react'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'
import { getRouteUrl } from '@/navigation/getRouteUrl'
import { useCreateCall } from './useCreateCall'

const COPIED_FEEDBACK_MS = 3000

/**
 * Create a room for later and copy its link. Browsers may refuse to write
 * the clipboard once the room is created (the click is too old, Safari):
 * `fallbackRoom` is then set, to show the link in LaterMeetingDialog.
 */
export const useInviteLink = () => {
  const { createRoomForLater, isPending, isError } = useCreateCall()
  const [isCopied, setIsCopied] = useState(false)
  const [fallbackRoom, setFallbackRoom] = useState<ApiRoom | null>(null)

  useEffect(() => {
    if (!isCopied) return
    const timeout = setTimeout(() => setIsCopied(false), COPIED_FEEDBACK_MS)
    return () => clearTimeout(timeout)
  }, [isCopied])

  const copyInviteLink = async () => {
    let room: ApiRoom
    try {
      room = await createRoomForLater()
    } catch {
      // shown from `isError`
      return
    }
    try {
      await navigator.clipboard.writeText(getRouteUrl('room', room.slug))
      setIsCopied(true)
    } catch {
      setFallbackRoom(room)
    }
  }

  return {
    copyInviteLink,
    isCopied,
    isPending,
    isError,
    fallbackRoom,
    closeFallback: () => setFallbackRoom(null),
  }
}
