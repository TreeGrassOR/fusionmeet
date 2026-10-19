/**
 * Whether remote audio can be amplified above 100 %.
 *
 * Without the livekit-client `webAudioMix` room option, participant volumes are
 * applied to HTMLMediaElement.volume, which only accepts values in [0, 1].
 * `webAudioMix` routes remote audio through an AudioContext GainNode instead,
 * but livekit-client then switches the speaker with AudioContext.setSinkId and
 * refuses to change the audio output where it does not exist (Firefox, Safari).
 * So it is only enabled where AudioContext.setSinkId is available.
 */
export const isVolumeBoostSupported = (): boolean => {
  try {
    return (
      typeof AudioContext !== 'undefined' &&
      'setSinkId' in AudioContext.prototype
    )
  } catch {
    return false
  }
}
