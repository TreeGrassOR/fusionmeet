/**
 * Whether an audio output device can still be selected.
 *
 * Without the media permission, browsers list devices with empty ids: the
 * device cannot be checked then, so it is assumed to be available.
 */
export const isAudioOutputAvailable = (
  devices: ReadonlyArray<Pick<MediaDeviceInfo, 'kind' | 'deviceId'>>,
  deviceId: string
): boolean => {
  const outputs = devices.filter((device) => device.kind === 'audiooutput')
  if (!outputs.some((device) => device.deviceId)) return true
  return outputs.some((device) => device.deviceId === deviceId)
}
