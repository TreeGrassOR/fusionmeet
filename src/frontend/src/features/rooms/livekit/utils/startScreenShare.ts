import { Track, type LocalParticipant, type LocalTrack } from 'livekit-client'
import { TrackSource } from '@livekit/protocol'
import {
  getScreenShareCaptureOptions,
  isRestrictOwnAudioSupported,
  mayCarryConferenceAudio,
  SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS,
  SCREEN_SHARE_PUBLISH_OPTIONS,
} from './screenShare'

/**
 * Why the captured screen share audio was not published:
 * - 'unsupported': the browser cannot remove its own playback from a capture
 *   (before Chrome/Edge 141);
 * - 'notApplied': it could, but did not for this capture (e.g. Linux).
 */
export type ScreenShareAudioDropReason = 'unsupported' | 'notApplied'

let isStarting = false

/**
 * livekit-client's own publish check (LocalParticipant
 * .hasPermissionsToPublish): an empty source list allows every source.
 * Publishing a source that is not allowed fails with a 403.
 */
const canPublishScreenShareAudio = (participant: LocalParticipant) => {
  const permissions = participant.permissions
  if (!permissions?.canPublish) return false
  const { canPublishSources } = permissions
  return (
    canPublishSources.length === 0 ||
    canPublishSources.includes(TrackSource.SCREEN_SHARE_AUDIO)
  )
}

/**
 * Captures and publishes the screen share. Unlike setScreenShareEnabled(),
 * which captures and publishes in one go, the audio is checked before it is
 * published and dropped when it may carry the conference: the other
 * participants would hear themselves again.
 *
 * Stopping goes through setScreenShareEnabled(false), which unpublishes both
 * screen sources, like the browser's own "Stop sharing" button does.
 *
 * Resolves with the reason when the audio was dropped, null otherwise
 * (published, none captured or not allowed, or a capture already in
 * progress).
 */
export const startScreenShare = async (
  participant: LocalParticipant
): Promise<ScreenShareAudioDropReason | null> => {
  // a second click, from any entry point, must not open a second picker
  if (isStarting || participant.isScreenShareEnabled) return null
  isStarting = true
  try {
    const tracks = await participant.createScreenTracks({
      ...getScreenShareCaptureOptions(),
      // without the permission, the browser does not even offer the audio
      ...(canPublishScreenShareAudio(participant)
        ? {}
        : { audio: false, systemAudio: 'exclude' }),
    })
    const video = tracks.find(
      (track) => track.source === Track.Source.ScreenShare
    )
    const audio = tracks.find(
      (track) => track.source === Track.Source.ScreenShareAudio
    )

    let dropReason: ScreenShareAudioDropReason | null = null
    let toPublish = tracks
    if (audio && !canPublishScreenShareAudio(participant)) {
      // the permission changed while the picker was open: its publish would
      // fail, and take the video down with it
      audio.stop()
      toPublish = tracks.filter((track) => track !== audio)
    } else if (
      audio &&
      mayCarryConferenceAudio(video?.mediaStreamTrack, audio.mediaStreamTrack)
    ) {
      dropReason = isRestrictOwnAudioSupported() ? 'notApplied' : 'unsupported'
      audio.stop()
      toPublish = tracks.filter((track) => track !== audio)
    }

    await publishScreenTracks(participant, toPublish)
    return dropReason
  } finally {
    isStarting = false
  }
}

const publishScreenTracks = async (
  participant: LocalParticipant,
  tracks: LocalTrack[]
) => {
  const results = await Promise.allSettled(
    tracks.map((track) =>
      participant.publishTrack(
        track,
        track.source === Track.Source.ScreenShareAudio
          ? SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS
          : SCREEN_SHARE_PUBLISH_OPTIONS
      )
    )
  )
  const failure = results.find(
    (result): result is PromiseRejectedResult => result.status === 'rejected'
  )
  if (!failure) return

  // never leave half a screen share (e.g. its audio alone) published
  await Promise.all(
    tracks.map(async (track, index) => {
      if (results[index].status === 'fulfilled') {
        await participant.unpublishTrack(track).catch(() => track.stop())
      } else {
        track.stop()
      }
    })
  )
  throw failure.reason
}
