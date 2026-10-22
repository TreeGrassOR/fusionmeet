import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  clampVolume,
  getMaxParticipantVolume,
  getParticipantAudioSettings,
  MAX_STORED_PARTICIPANTS,
  parseParticipantAudioState,
  pruneParticipants,
  resolveParticipantPlayback,
  serializeParticipantAudioState,
  type ParticipantAudioSettings,
  type StoredParticipantAudioSettings,
} from './participantAudio'
import { STORAGE_KEYS } from '@/utils/storageKeys'

const KEY = STORAGE_KEYS.PARTICIPANT_AUDIO

class MemoryStorage implements Storage {
  private data = new Map<string, string>()
  get length() {
    return this.data.size
  }
  clear() {
    this.data.clear()
  }
  getItem(key: string) {
    return this.data.has(key) ? this.data.get(key)! : null
  }
  key(index: number) {
    return Array.from(this.data.keys())[index] ?? null
  }
  removeItem(key: string) {
    this.data.delete(key)
  }
  setItem(key: string, value: string) {
    this.data.set(key, String(value))
  }
}

// valtio notifies subscribers asynchronously
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const SCREEN_SHARE_DEFAULTS = { screenShareVolume: 1, screenShareMuted: false }

/** Stored entry with the screen share fields at their defaults. */
const stored = (
  volume: number,
  mutedForMe: boolean,
  updatedAt: number,
  extra: Partial<StoredParticipantAudioSettings> = {}
): StoredParticipantAudioSettings => ({
  volume,
  mutedForMe,
  ...SCREEN_SHARE_DEFAULTS,
  updatedAt,
  ...extra,
})

const settings = (
  patch: Partial<ParticipantAudioSettings> = {}
): ParticipantAudioSettings => ({
  volume: 1,
  mutedForMe: false,
  ...SCREEN_SHARE_DEFAULTS,
  ...patch,
})

const loadStore = async () => {
  vi.resetModules()
  return await import('./participantAudio')
}

describe('clampVolume', () => {
  it('keeps values within [0, 2] and rounds to the percent', () => {
    expect(clampVolume(0.5)).toBe(0.5)
    expect(clampVolume(1.234)).toBe(1.23)
    expect(clampVolume(-1)).toBe(0)
    expect(clampVolume(5)).toBe(2)
  })

  it('caps at the given maximum, never above 200 %', () => {
    expect(clampVolume(1.5, 1)).toBe(1)
    expect(clampVolume(1.5, 3)).toBe(1.5)
    expect(clampVolume(2.5, 3)).toBe(2)
    expect(clampVolume(0.5, -1)).toBe(0)
  })

  it('falls back to the default volume on invalid input', () => {
    expect(clampVolume(Number.NaN)).toBe(1)
    expect(clampVolume(Number.POSITIVE_INFINITY)).toBe(1)
    expect(clampVolume('0.5')).toBe(1)
    expect(clampVolume(undefined)).toBe(1)
    expect(clampVolume(null, 0.5)).toBe(0.5)
  })
})

describe('getMaxParticipantVolume', () => {
  it('offers 200 % only with the volume boost', () => {
    expect(getMaxParticipantVolume(true)).toBe(2)
    expect(getMaxParticipantVolume(false)).toBe(1)
  })
})

describe('resolveParticipantPlayback', () => {
  it('silences both sources of participants muted for me', () => {
    expect(
      resolveParticipantPlayback(
        settings({ volume: 1.5, mutedForMe: true, screenShareVolume: 0.5 }),
        2
      )
    ).toEqual({
      microphone: { volume: 1.5, isMuted: true },
      screenShareAudio: { volume: 0.5, isMuted: true },
    })
  })

  it('silences a source at volume 0 only', () => {
    expect(resolveParticipantPlayback(settings({ volume: 0 }), 2)).toEqual({
      microphone: { volume: 0, isMuted: true },
      screenShareAudio: { volume: 1, isMuted: false },
    })
    expect(
      resolveParticipantPlayback(settings({ screenShareVolume: 0 }), 2)
    ).toEqual({
      microphone: { volume: 1, isMuted: false },
      screenShareAudio: { volume: 0, isMuted: true },
    })
  })

  it('mutes the screen share audio and keeps the microphone audible', () => {
    expect(
      resolveParticipantPlayback(
        settings({
          volume: 0.8,
          screenShareVolume: 1.2,
          screenShareMuted: true,
        }),
        2
      )
    ).toEqual({
      microphone: { volume: 0.8, isMuted: false },
      screenShareAudio: { volume: 1.2, isMuted: true },
    })
  })

  it('caps both volumes to what the browser supports', () => {
    expect(
      resolveParticipantPlayback(
        settings({ volume: 1.5, screenShareVolume: 1.8 }),
        1
      )
    ).toEqual({
      microphone: { volume: 1, isMuted: false },
      screenShareAudio: { volume: 1, isMuted: false },
    })
    expect(
      resolveParticipantPlayback(
        settings({ volume: 1.5, screenShareVolume: 1.8 }),
        2
      )
    ).toEqual({
      microphone: { volume: 1.5, isMuted: false },
      screenShareAudio: { volume: 1.8, isMuted: false },
    })
  })
})

describe('parseParticipantAudioState', () => {
  it('returns an empty state for missing or malformed values', () => {
    for (const raw of [
      null,
      undefined,
      '',
      'not json',
      '[]',
      '42',
      '{"participants": []}',
      '{"participants": "x"}',
    ]) {
      expect(parseParticipantAudioState(raw)).toEqual({ participants: {} })
    }
  })

  it('sanitizes entries and drops invalid or default ones', () => {
    const raw = JSON.stringify({
      version: 1,
      participants: {
        alice: { volume: 1.5, mutedForMe: false, updatedAt: 10 },
        bob: { volume: 7, mutedForMe: 'yes', updatedAt: 'later' },
        carol: { volume: 1, mutedForMe: false, updatedAt: 3 },
        dave: { mutedForMe: true },
        eve: 'loud',
        '': { volume: 0.5, mutedForMe: false, updatedAt: 1 },
      },
    })
    expect(parseParticipantAudioState(raw)).toEqual({
      participants: {
        alice: stored(1.5, false, 10),
        bob: stored(2, false, 0),
        dave: stored(1, true, 0),
      },
    })
  })

  it('loads version 1 entries with the screen share defaults', () => {
    const state = parseParticipantAudioState(
      '{"version":1,"participants":{"a":{"volume":0.5,"mutedForMe":false,"updatedAt":1}}}'
    )
    expect(state).toEqual({
      participants: {
        a: {
          volume: 0.5,
          mutedForMe: false,
          screenShareVolume: 1,
          screenShareMuted: false,
          updatedAt: 1,
        },
      },
    })
  })

  it('sanitizes the screen share fields', () => {
    const raw = JSON.stringify({
      version: 2,
      participants: {
        loud: { screenShareVolume: 9, updatedAt: 1 },
        quiet: { screenShareVolume: -3, updatedAt: 2 },
        invalid: { screenShareVolume: '0.5', screenShareMuted: 'yes' },
        muted: { screenShareMuted: true, updatedAt: 3 },
      },
    })
    expect(parseParticipantAudioState(raw)).toEqual({
      participants: {
        loud: stored(1, false, 1, { screenShareVolume: 2 }),
        quiet: stored(1, false, 2, { screenShareVolume: 0 }),
        muted: stored(1, false, 3, { screenShareMuted: true }),
      },
    })
  })

  it('ignores a __proto__ identity', () => {
    const state = parseParticipantAudioState(
      '{"participants": {"__proto__": {"volume": 0.5, "mutedForMe": true}}}'
    )
    expect(Object.keys(state.participants)).toEqual([])
    expect(Object.getPrototypeOf(state.participants)).toBe(Object.prototype)
  })

  it('keeps only the most recently updated entries', () => {
    const participants: Record<string, StoredParticipantAudioSettings> = {}
    for (let i = 0; i < MAX_STORED_PARTICIPANTS + 5; i++) {
      participants[`p${i}`] = stored(0.5, false, i)
    }
    const state = parseParticipantAudioState(JSON.stringify({ participants }))
    const identities = Object.keys(state.participants)
    expect(identities).toHaveLength(MAX_STORED_PARTICIPANTS)
    expect(identities).not.toContain('p0')
    expect(identities).not.toContain('p4')
    expect(identities).toContain('p5')
    expect(identities).toContain(`p${MAX_STORED_PARTICIPANTS + 4}`)
  })

  it('round-trips with serializeParticipantAudioState', () => {
    const state = {
      participants: {
        alice: stored(0.25, true, 42),
        bob: stored(1, false, 43, {
          screenShareVolume: 0.4,
          screenShareMuted: true,
        }),
      },
    }
    const serialized = serializeParticipantAudioState(state)
    expect(JSON.parse(serialized)).toEqual({ version: 2, ...state })
    expect(parseParticipantAudioState(serialized)).toEqual(state)
  })
})

describe('pruneParticipants', () => {
  it('returns the same object when under the limit', () => {
    const participants = { a: stored(0.5, false, 1) }
    expect(pruneParticipants(participants, 1)).toBe(participants)
  })

  it('drops the oldest entries above the limit', () => {
    expect(
      Object.keys(
        pruneParticipants(
          {
            old: stored(0.5, false, 1),
            recent: stored(0.5, false, 3),
            middle: stored(0.5, false, 2),
          },
          2
        )
      ).sort()
    ).toEqual(['middle', 'recent'])
  })
})

describe('getParticipantAudioSettings', () => {
  it('returns defaults for unknown identities', () => {
    const state = parseParticipantAudioState(null)
    expect(getParticipantAudioSettings(state, 'unknown')).toEqual(settings())
    expect(getParticipantAudioSettings(state, 'toString')).toEqual(settings())
  })

  it('returns every stored field, without updatedAt', () => {
    const state = {
      participants: {
        alice: stored(0.5, true, 7, {
          screenShareVolume: 1.5,
          screenShareMuted: true,
        }),
      },
    }
    expect(getParticipantAudioSettings(state, 'alice')).toEqual({
      volume: 0.5,
      mutedForMe: true,
      screenShareVolume: 1.5,
      screenShareMuted: true,
    })
  })
})

describe('participantAudioStore', () => {
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    vi.stubGlobal('localStorage', storage)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('restores the settings persisted by identity', async () => {
    storage.setItem(
      KEY,
      JSON.stringify({
        version: 1,
        participants: {
          alice: { volume: 1.8, mutedForMe: false, updatedAt: 1 },
          bob: { volume: 1, mutedForMe: true, updatedAt: 2 },
        },
      })
    )
    const store = await loadStore()
    expect(
      store.getParticipantAudioSettings(store.participantAudioStore, 'alice')
    ).toEqual(settings({ volume: 1.8 }))
    expect(
      store.getParticipantAudioSettings(store.participantAudioStore, 'bob')
    ).toEqual(settings({ mutedForMe: true }))
  })

  it('persists volume and mute changes per identity', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(1_000)
    const store = await loadStore()

    store.setParticipantVolume('alice', 1.5)
    store.toggleParticipantMutedForMe('bob')
    await flush()

    expect(JSON.parse(storage.getItem(KEY)!)).toEqual({
      version: 2,
      participants: {
        alice: stored(1.5, false, 1_000),
        bob: stored(1, true, 1_000),
      },
    })
  })

  it('persists the screen share settings apart from the voice', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(2_000)
    const store = await loadStore()

    store.setParticipantScreenShareVolume('alice', 0.4)
    store.toggleParticipantScreenShareMuted('bob')
    await flush()

    expect(JSON.parse(storage.getItem(KEY)!)).toEqual({
      version: 2,
      participants: {
        alice: stored(1, false, 2_000, { screenShareVolume: 0.4 }),
        bob: stored(1, false, 2_000, { screenShareMuted: true }),
      },
    })

    // survives a reload
    const reloaded = await loadStore()
    expect(
      reloaded.getParticipantAudioSettings(
        reloaded.participantAudioStore,
        'alice'
      )
    ).toEqual(settings({ screenShareVolume: 0.4 }))
    expect(
      reloaded.getParticipantAudioSettings(
        reloaded.participantAudioStore,
        'bob'
      )
    ).toEqual(settings({ screenShareMuted: true }))
  })

  it('keeps the other fields when one of them changes', async () => {
    const store = await loadStore()
    store.setParticipantVolume('alice', 0.5)
    store.setParticipantScreenShareVolume('alice', 1.5)
    store.setParticipantScreenShareMuted('alice', true)
    store.setParticipantMutedForMe('alice', true)
    expect(
      store.getParticipantAudioSettings(store.participantAudioStore, 'alice')
    ).toEqual({
      volume: 0.5,
      mutedForMe: true,
      screenShareVolume: 1.5,
      screenShareMuted: true,
    })
  })

  it('does not touch the entry when nothing changes', async () => {
    const store = await loadStore()
    store.setParticipantScreenShareVolume('alice', 0.4)
    const before = store.participantAudioStore.participants.alice
    store.setParticipantScreenShareVolume('alice', 0.4)
    store.setParticipantScreenShareMuted('alice', false)
    expect(store.participantAudioStore.participants.alice).toBe(before)
  })

  it('clamps stored volumes to [0, 200 %]', async () => {
    const store = await loadStore()
    store.setParticipantVolume('alice', 3)
    store.setParticipantVolume('bob', -1)
    expect(store.participantAudioStore.participants.alice.volume).toBe(2)
    expect(store.participantAudioStore.participants.bob.volume).toBe(0)

    store.setParticipantScreenShareVolume('carol', 3)
    store.setParticipantScreenShareVolume('dave', -1)
    store.setParticipantScreenShareVolume('eve', 0.333)
    const { participants } = store.participantAudioStore
    expect(participants.carol.screenShareVolume).toBe(2)
    expect(participants.dave.screenShareVolume).toBe(0)
    expect(participants.eve.screenShareVolume).toBe(0.33)
  })

  it('removes entries back to the defaults', async () => {
    const store = await loadStore()
    store.setParticipantVolume('alice', 0.5)
    store.setParticipantMutedForMe('alice', true)
    await flush()
    expect(JSON.parse(storage.getItem(KEY)!).participants.alice).toMatchObject({
      volume: 0.5,
      mutedForMe: true,
    })

    store.setParticipantVolume('alice', 1)
    store.toggleParticipantMutedForMe('alice')
    await flush()
    expect(store.participantAudioStore.participants).toEqual({})
    expect(JSON.parse(storage.getItem(KEY)!).participants).toEqual({})
  })

  it('removes an entry when the screen share settings go back to the defaults', async () => {
    const store = await loadStore()
    store.setParticipantScreenShareVolume('alice', 0.4)
    store.toggleParticipantScreenShareMuted('alice')
    await flush()
    expect(JSON.parse(storage.getItem(KEY)!).participants.alice).toMatchObject({
      screenShareVolume: 0.4,
      screenShareMuted: true,
    })

    store.toggleParticipantScreenShareMuted('alice')
    expect(store.participantAudioStore.participants.alice).toBeDefined()
    store.setParticipantScreenShareVolume('alice', 1)
    await flush()
    expect(store.participantAudioStore.participants).toEqual({})
    expect(JSON.parse(storage.getItem(KEY)!).participants).toEqual({})
  })

  it('ignores invalid identities', async () => {
    const store = await loadStore()
    store.setParticipantVolume('', 0.5)
    store.setParticipantMutedForMe('__proto__', true)
    expect(Object.keys(store.participantAudioStore.participants)).toEqual([])
  })

  it('keeps the newest entries when the limit is reached', async () => {
    const participants: Record<string, StoredParticipantAudioSettings> = {}
    for (let i = 0; i < MAX_STORED_PARTICIPANTS; i++) {
      participants[`p${i}`] = stored(0.5, false, i)
    }
    storage.setItem(KEY, JSON.stringify({ version: 1, participants }))
    const store = await loadStore()

    store.setParticipantVolume('newcomer', 1.2)
    const identities = Object.keys(store.participantAudioStore.participants)
    expect(identities).toHaveLength(MAX_STORED_PARTICIPANTS)
    expect(identities).toContain('newcomer')
    expect(identities).not.toContain('p0')
  })

  it('works when the storage is unavailable or full', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('SecurityError')
      },
      setItem: () => {
        throw new Error('QuotaExceededError')
      },
    })
    const store = await loadStore()
    store.setParticipantVolume('alice', 0.3)
    await flush()
    expect(
      store.getParticipantAudioSettings(store.participantAudioStore, 'alice')
    ).toEqual(settings({ volume: 0.3 }))

    vi.stubGlobal('localStorage', undefined)
    const storeWithoutStorage = await loadStore()
    storeWithoutStorage.setParticipantMutedForMe('alice', true)
    await flush()
    expect(
      storeWithoutStorage.participantAudioStore.participants.alice.mutedForMe
    ).toBe(true)
  })
})
