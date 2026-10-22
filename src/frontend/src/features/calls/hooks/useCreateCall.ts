import { useSnapshot } from 'valtio'
import { generateRoomId, useCreateRoom } from '@/features/rooms'
import { navigateTo } from '@/navigation/navigateTo'
import { userStore } from '@/stores/user'

/**
 * Create a room, like the "Create a meeting" menu of the home page: to start
 * a call now, or to share its link for later.
 */
export const useCreateCall = () => {
  const { username } = useSnapshot(userStore)
  const { mutateAsync: createRoom, isPending, isError } = useCreateRoom()

  const createRoomForLater = () =>
    createRoom({ slug: generateRoomId(), username })

  const startCall = () =>
    createRoomForLater()
      .then((data) =>
        navigateTo('room', data.slug, {
          state: { create: true, initialRoomData: data },
        })
      )
      // the error is shown from `isError`
      .catch(() => undefined)

  return { startCall, createRoomForLater, isPending, isError }
}
