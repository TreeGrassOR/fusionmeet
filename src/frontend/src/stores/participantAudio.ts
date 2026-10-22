import { proxy, subscribe } from 'valtio'
import { STORAGE_KEYS } from '@/utils/storageKeys'

/**
 * Local, per-identity playback settings of remote participants ("volume" and
 * "mute for me", for the voice and for the screen share audio). They only
 * affect what the current user hears and are kept in localStorage, keyed by
 * the LiveKit participant identity.
 */

export const DEFAULT_PARTICIPANT_VOLUME = 1
/** Highest gain (200 %), only reachable when audio goes through Web Audio. */
export const MAX_PARTICIPANT_VOLUME = 2
/** Without Web Audio, livekit-client sets HTMLMediaElement.volume (0..1). */
export const MAX_PARTICIPANT_VOLUME_WITHOUT_BOOST = 1
/** Oldest entries are dropped beyond this count to keep the storage small. */
export const MAX_STORED_PARTICIPANTS = 200

// 2: screen share audio fields. Version 1 entries load with their defaults,
// and an older build reading version 2 only loses them.
const STORAGE_VERSION = 2

export type ParticipantAudioSettings = {
  /** Gain applied to the participant microphone, 1 = 100 % */
  volume: number
  /** Participant (voice and screen share audio) is silenced for the current user only */
  mutedForMe: boolean
  /** Gain applied to the participant screen share audio, 1 = 100 % */
  screenShareVolume: number
  /** Screen share audio only is silenced for the current user */
  screenShareMuted: boolean
}

export type StoredParticipantAudioSettings = ParticipantAudioSettings & {
  /** Epoch ms of the last change, used to prune old entries */
  updatedAt: number
}

export type ParticipantAudioState = {
  participants: Record<string, StoredParticipantAudioSettings>
}

type ReadonlyParticipantAudioState = {
  readonly participants: Readonly<
    Record<string, Readonly<ParticipantAudioSettings>>
  >
}

export const DEFAULT_PARTICIPANT_AUDIO_SETTINGS: Readonly<ParticipantAudioSettings> =
  Object.freeze({
    volume: DEFAULT_PARTICIPANT_VOLUME,
    mutedForMe: false,
    screenShareVolume: DEFAULT_PARTICIPANT_VOLUME,
    screenShareMuted: false,
  })

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

// '__proto__' would change the prototype of the plain objects used as maps
const isValidIdentity = (identity: unknown): identity is string =>
  typeof identity === 'string' &&
  identity.length > 0 &&
  identity !== '__proto__'

/**
 * Bounds a volume to [0, maxVolume] (maxVolume itself never exceeds 200 %),
 * rounded to the percent. Invalid values fall back to the default volume.
 */
export const clampVolume = (
  volume: unknown,
  maxVolume: number = MAX_PARTICIPANT_VOLUME
): number => {
  const max = Math.min(Math.max(maxVolume, 0), MAX_PARTICIPANT_VOLUME)
  const value =
    typeof volume === 'number' && Number.isFinite(volume)
      ? volume
      : DEFAULT_PARTICIPANT_VOLUME
  return Math.round(Math.min(Math.max(value, 0), max) * 100) / 100
}

export const getMaxParticipantVolume = (isVolumeBoostEnabled: boolean) =>
  isVolumeBoostEnabled
    ? MAX_PARTICIPANT_VOLUME
    : MAX_PARTICIPANT_VOLUME_WITHOUT_BOOST

export const isDefaultParticipantAudioSettings = (
  settings: Readonly<ParticipantAudioSettings>
) =>
  settings.volume === DEFAULT_PARTICIPANT_VOLUME &&
  !settings.mutedForMe &&
  settings.screenShareVolume === DEFAULT_PARTICIPANT_VOLUME &&
  !settings.screenShareMuted

const isSameParticipantAudioSettings = (
  a: Readonly<ParticipantAudioSettings>,
  b: Readonly<ParticipantAudioSettings>
) =>
  a.volume === b.volume &&
  a.mutedForMe === b.mutedForMe &&
  a.screenShareVolume === b.screenShareVolume &&
  a.screenShareMuted === b.screenShareMuted

/**
 * Keeps the `max` most recently updated entries.
 */
export const pruneParticipants = (
  participants: Record<string, StoredParticipantAudioSettings>,
  max: number = MAX_STORED_PARTICIPANTS
): Record<string, StoredParticipantAudioSettings> => {
  const entries = Object.entries(participants)
  if (entries.length <= max) return participants
  return Object.fromEntries(
    entries.sort(([, a], [, b]) => b.updatedAt - a.updatedAt).slice(0, max)
  )
}

/**
 * Parses the persisted value, dropping anything malformed or equal to the
 * defaults. Never throws.
 */
export const parseParticipantAudioState = (
  raw: string | null | undefined
): ParticipantAudioState => {
  const participants: Record<string, StoredParticipantAudioSettings> = {}
  if (!raw) return { participants }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { participants }
  }
  if (!isRecord(parsed) || !isRecord(parsed.participants)) {
    return { participants }
  }

  for (const [identity, value] of Object.entries(parsed.participants)) {
    if (!isValidIdentity(identity) || !isRecord(value)) continue
    const settings: StoredParticipantAudioSettings = {
      volume: clampVolume(value.volume),
      mutedForMe: value.mutedForMe === true,
      // absent from version 1 entries: defaults
      screenShareVolume: clampVolume(value.screenShareVolume),
      screenShareMuted: value.screenShareMuted === true,
      updatedAt:
        typeof value.updatedAt === 'number' && Number.isFinite(value.updatedAt)
          ? value.updatedAt
          : 0,
    }
    if (isDefaultParticipantAudioSettings(settings)) continue
    participants[identity] = settings
  }
  return { participants: pruneParticipants(participants) }
}

export const serializeParticipantAudioState = (
  state: ParticipantAudioState
): string =>
  JSON.stringify({
    version: STORAGE_VERSION,
    participants: state.participants,
  })

/**
 * Settings of a participant, defaults when nothing is stored.
 * Works on the store and on its snapshots.
 */
export const getParticipantAudioSettings = (
  state: ReadonlyParticipantAudioState,
  identity: string
): ParticipantAudioSettings => {
  const stored = Object.prototype.hasOwnProperty.call(
    state.participants,
    identity
  )
    ? state.participants[identity]
    : undefined
  if (!stored) return { ...DEFAULT_PARTICIPANT_AUDIO_SETTINGS }
  return {
    volume: stored.volume,
    mutedForMe: stored.mutedForMe,
    screenShareVolume: stored.screenShareVolume,
    screenShareMuted: stored.screenShareMuted,
  }
}

export type SourcePlayback = {
  /** Gain to apply, within the bounds the browser supports */
  volume: number
  /** Whether the source must be silenced (muted for me, or volume 0) */
  isMuted: boolean
}

export type ParticipantPlayback = {
  microphone: SourcePlayback
  screenShareAudio: SourcePlayback
}

/**
 * "Mute for me" silences both sources; the screen share mute keeps the
 * microphone audible.
 */
export const resolveParticipantPlayback = (
  settings: Readonly<ParticipantAudioSettings>,
  maxVolume: number
): ParticipantPlayback => {
  const microphone = clampVolume(settings.volume, maxVolume)
  const screenShareAudio = clampVolume(settings.screenShareVolume, maxVolume)
  return {
    microphone: {
      volume: microphone,
      isMuted: settings.mutedForMe || microphone === 0,
    },
    screenShareAudio: {
      volume: screenShareAudio,
      isMuted:
        settings.mutedForMe ||
        settings.screenShareMuted ||
        screenShareAudio === 0,
    },
  }
}

const getStorage = (): Storage | undefined => {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    // access can throw when storage is disabled
    return undefined
  }
}

const readStoredState = (): ParticipantAudioState => {
  try {
    return parseParticipantAudioState(
      getStorage()?.getItem(STORAGE_KEYS.PARTICIPANT_AUDIO)
    )
  } catch {
    return { participants: {} }
  }
}

export const participantAudioStore =
  proxy<ParticipantAudioState>(readStoredState())

subscribe(participantAudioStore, () => {
  try {
    getStorage()?.setItem(
      STORAGE_KEYS.PARTICIPANT_AUDIO,
      serializeParticipantAudioState(participantAudioStore)
    )
  } catch {
    // quota exceeded or storage disabled: keep the settings in memory only
  }
})

const updateParticipantAudioSettings = (
  identity: string,
  patch: Partial<ParticipantAudioSettings>
) => {
  if (!isValidIdentity(identity)) return
  const current = getParticipantAudioSettings(participantAudioStore, identity)
  const next: ParticipantAudioSettings = {
    volume: clampVolume(patch.volume ?? current.volume),
    mutedForMe: patch.mutedForMe ?? current.mutedForMe,
    screenShareVolume: clampVolume(
      patch.screenShareVolume ?? current.screenShareVolume
    ),
    screenShareMuted: patch.screenShareMuted ?? current.screenShareMuted,
  }
  if (isSameParticipantAudioSettings(next, current)) return
  if (isDefaultParticipantAudioSettings(next)) {
    delete participantAudioStore.participants[identity]
    return
  }
  participantAudioStore.participants[identity] = {
    ...next,
    updatedAt: Date.now(),
  }
  const pruned = pruneParticipants(participantAudioStore.participants)
  if (pruned !== participantAudioStore.participants) {
    participantAudioStore.participants = pruned
  }
}

export const setParticipantVolume = (identity: string, volume: number) =>
  updateParticipantAudioSettings(identity, { volume })

export const setParticipantMutedForMe = (
  identity: string,
  mutedForMe: boolean
) => updateParticipantAudioSettings(identity, { mutedForMe })

export const toggleParticipantMutedForMe = (identity: string) =>
  setParticipantMutedForMe(
    identity,
    !getParticipantAudioSettings(participantAudioStore, identity).mutedForMe
  )

export const setParticipantScreenShareVolume = (
  identity: string,
  screenShareVolume: number
) => updateParticipantAudioSettings(identity, { screenShareVolume })

export const setParticipantScreenShareMuted = (
  identity: string,
  screenShareMuted: boolean
) => updateParticipantAudioSettings(identity, { screenShareMuted })

export const toggleParticipantScreenShareMuted = (identity: string) =>
  setParticipantScreenShareMuted(
    identity,
    !getParticipantAudioSettings(participantAudioStore, identity)
      .screenShareMuted
  )
