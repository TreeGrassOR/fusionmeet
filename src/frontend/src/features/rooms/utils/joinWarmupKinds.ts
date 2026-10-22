import type { PermissionKind } from '@/stores/permissions'

export type JoinWarmupKinds = {
  audio: boolean
  video: boolean
}

const isPermissionGranted = async (name: PermissionKind): Promise<boolean> => {
  try {
    const status = await navigator.permissions.query({
      name: name as PermissionName,
    })
    return status.state === 'granted'
  } catch {
    // No Permissions API, or this kind cannot be queried: warm up as before.
    return false
  }
}

/**
 * Device kinds the join screen must warm up before acquiring its preview
 * tracks. A kind the user joins with turned off is not requested, so the
 * camera does not light up when "join with the camera off" is set. A kind the
 * browser already allows is not requested either: its device labels are
 * readable and its track can be acquired without a dialog. Trade-off: an off
 * kind that was never allowed shows its own dialog when it is first enabled.
 */
export const resolveJoinWarmupKinds = async ({
  audioEnabled,
  videoEnabled,
}: {
  audioEnabled: boolean
  videoEnabled: boolean
}): Promise<JoinWarmupKinds> => {
  const [audioGranted, videoGranted] = await Promise.all([
    audioEnabled && isPermissionGranted('microphone'),
    videoEnabled && isPermissionGranted('camera'),
  ])
  return {
    audio: audioEnabled && !audioGranted,
    video: videoEnabled && !videoGranted,
  }
}
