import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useRoomContext } from '@livekit/components-react'
import {
  ConnectionState,
  RoomEvent,
  Track,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client'
import { subscribe } from 'valtio'
import {
  getMaxParticipantVolume,
  getParticipantAudioSettings,
  participantAudioStore,
  resolveParticipantPlayback,
} from '@/stores/participantAudio'
import { reportError } from '@/features/analytics/telemetry'
import { applyParticipantPlayback } from '../utils/applyParticipantPlayback'
import { isAudioOutputAvailable } from '../utils/audioOutput'

/**
 * Headless component, mounted next to RoomAudioRenderer, that applies the
 * local volume / "mute for me" settings to remote participants, and re-applies
 * them whenever participants or their audio tracks (re)appear.
 */
export const ParticipantAudioSynchronizer = () => {
  const room = useRoomContext()

  // Load the namespace early, so menus and tiles never wait for it.
  useTranslation('participantAudio', { useSuspense: false })

  useEffect(() => {
    const maxVolume = getMaxParticipantVolume(!!room.options.webAudioMix)

    const apply = (participant: RemoteParticipant) =>
      applyParticipantPlayback(
        participant,
        resolveParticipantPlayback(
          getParticipantAudioSettings(
            participantAudioStore,
            participant.identity
          ),
          maxVolume
        )
      )

    const applyAll = () => room.remoteParticipants.forEach(apply)

    // Also on unpublish: while a track is republished, the participant briefly
    // holds two publications of the same source, and setVolume() only reaches
    // the first one; re-apply once the previous one is gone.
    const onPublicationChanged = (
      publication: RemoteTrackPublication,
      participant: RemoteParticipant
    ) => {
      if (publication.kind === Track.Kind.Audio) apply(participant)
    }

    const onTrackSubscribed = (
      _track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: RemoteParticipant
    ) => {
      if (publication.kind === Track.Kind.Audio) apply(participant)
    }

    applyAll()
    const unsubscribe = subscribe(participantAudioStore, applyAll)
    room
      .on(RoomEvent.ParticipantConnected, apply)
      .on(RoomEvent.TrackPublished, onPublicationChanged)
      .on(RoomEvent.TrackUnpublished, onPublicationChanged)
      .on(RoomEvent.TrackSubscribed, onTrackSubscribed)
      .on(RoomEvent.Connected, applyAll)
      .on(RoomEvent.Reconnected, applyAll)

    return () => {
      unsubscribe()
      room
        .off(RoomEvent.ParticipantConnected, apply)
        .off(RoomEvent.TrackPublished, onPublicationChanged)
        .off(RoomEvent.TrackUnpublished, onPublicationChanged)
        .off(RoomEvent.TrackSubscribed, onTrackSubscribed)
        .off(RoomEvent.Connected, applyAll)
        .off(RoomEvent.Reconnected, applyAll)
    }
  }, [room])

  useEffect(() => {
    if (!room.options.webAudioMix) return

    // With webAudioMix, livekit-client plays remote audio through an
    // AudioContext created on connect. The output device from the room
    // options is applied at construction, before that context exists, so the
    // speaker would silently fall back to the system default: re-apply it.
    const restoreAudioOutput = async () => {
      const deviceId = room.options.audioOutput?.deviceId
      if (!deviceId || deviceId === 'default') return
      try {
        // A saved speaker that is gone would make livekit-client leave an
        // unhandled AudioContext.setSinkId rejection: keep the default then.
        const devices = await navigator.mediaDevices.enumerateDevices()
        if (!isAudioOutputAvailable(devices, deviceId)) return
        await room.switchActiveDevice('audiooutput', deviceId)
      } catch (error) {
        reportError('device_switch_failure', error, {
          at: 'ParticipantAudioSynchronizer.restoreAudioOutput',
        })
      }
    }
    const onConnected = () => void restoreAudioOutput()

    if (room.state === ConnectionState.Connected) onConnected()
    room.on(RoomEvent.Connected, onConnected)
    return () => {
      room.off(RoomEvent.Connected, onConnected)
    }
  }, [room])

  return null
}
