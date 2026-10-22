import {
  AudioPresets,
  ScreenSharePresets,
  type ScreenShareCaptureOptions,
  type TrackPublishOptions,
} from 'livekit-client'
import { isSafari } from '@/utils/livekit'

/**
 * Capture and publish options of the screen share, shared by every entry
 * point (control bar, picture-in-picture menu).
 */

type RestrictOwnAudioSettings = MediaTrackSettings & {
  restrictOwnAudio?: boolean
}

/**
 * Chrome/Edge 141+ can remove the page's own playback (the other
 * participants) from a screen or window capture with system audio. Chromium
 * also reports the constraint where it cannot apply it (Linux, likely
 * Windows 10): always check the captured track with mayCarryConferenceAudio().
 */
export const isRestrictOwnAudioSupported = (): boolean => {
  try {
    const supported = navigator.mediaDevices?.getSupportedConstraints?.() as
      | (MediaTrackSupportedConstraints & { restrictOwnAudio?: boolean })
      | undefined
    return supported?.restrictOwnAudio === true
  } catch {
    return false
  }
}

export const buildScreenShareCaptureOptions = ({
  canRestrictOwnAudio,
  isSafari,
}: {
  canRestrictOwnAudio: boolean
  isSafari: boolean
}): ScreenShareCaptureOptions => ({
  // a plain `true` (ideal): getDisplayMedia rejects exact/min with a TypeError
  audio: canRestrictOwnAudio ? { restrictOwnAudio: true } : true,
  // the meeting tab's own audio is the other participants, and
  // restrictOwnAudio does not apply to tab capture
  selfBrowserSurface: 'exclude',
  // without restrictOwnAudio, system audio would carry the conference itself
  systemAudio: canRestrictOwnAudio ? 'include' : 'exclude',
  // 15 fps capture, like the encoding, instead of livekit's 30 fps default;
  // not on Safari, where a resolution constraint gives a low-res capture on
  // Safari 17 (WebKit 263015)
  ...(isSafari ? {} : { resolution: ScreenSharePresets.h1080fps15.resolution }),
})

let captureOptions: ScreenShareCaptureOptions | undefined

/**
 * Computed once per page. livekit-client writes a default resolution into
 * the options it gets: pass a copy.
 */
export const getScreenShareCaptureOptions = (): ScreenShareCaptureOptions => {
  captureOptions ??= buildScreenShareCaptureOptions({
    canRestrictOwnAudio: isRestrictOwnAudioSupported(),
    isSafari: isSafari(),
  })
  return captureOptions
}

/**
 * Whether a captured screen share audio may contain the conference: a screen
 * or window capture whose audio the browser did not restrict. Tab audio only
 * holds that tab, and the meeting tab is excluded from the picker.
 */
export const mayCarryConferenceAudio = (
  video: MediaStreamTrack | undefined,
  audio: MediaStreamTrack
): boolean => {
  if (video?.getSettings().displaySurface === 'browser') return false
  return (
    (audio.getSettings() as RestrictOwnAudioSettings).restrictOwnAudio !== true
  )
}

/**
 * Screen share publish options; video fields are ignored for the audio
 * track, which also gets the fields of SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS.
 * Cameras keep the room default codec.
 */
export const SCREEN_SHARE_PUBLISH_OPTIONS: Readonly<TrackPublishOptions> =
  Object.freeze({
    // With VP9 (the room default), livekit-client 2.21 publishes screen share
    // as L1T3 with contentHint 'motion': a single 1080p layer the server
    // cannot downscale for slow viewers, which then only get fewer frames,
    // and camera-style rate control spending the whole bitrate cap. VP8 with
    // simulcast adds a 960x540 layer and keeps Chrome's screen content mode.
    videoCodec: 'vp8',
    simulcast: true,
    // 2.5 Mbps, 15 fps: the library default, explicit
    screenShareEncoding: ScreenSharePresets.h1080fps15.encoding,
    // screen share audio is stereo (music, videos), 64 kbps
    audioPreset: AudioPresets.musicStereo,
  })

/**
 * Screen share audio options. livekit-client 2.21 defaults forceStereo to
 * false, which wins over the stereo capture: the track would go out in mono,
 * with RED and DTX meant for speech. Not on the video track, which would be
 * announced as stereo too.
 */
export const SCREEN_SHARE_AUDIO_PUBLISH_OPTIONS: Readonly<TrackPublishOptions> =
  Object.freeze({
    ...SCREEN_SHARE_PUBLISH_OPTIONS,
    forceStereo: true,
    dtx: false,
    red: false,
  })
