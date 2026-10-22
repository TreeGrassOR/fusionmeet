import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { STORAGE_KEYS } from '@/utils/storageKeys'

const KEY = STORAGE_KEYS.USER_PREFERENCES

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

const loadStore = async () => {
  vi.resetModules()
  return (await import('./userPreferences')).userPreferencesStore
}

describe('userPreferencesStore join media defaults', () => {
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    vi.stubGlobal('localStorage', storage)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('defaults both switches to off', async () => {
    const store = await loadStore()
    expect(store.join_with_microphone_off).toBe(false)
    expect(store.join_with_camera_off).toBe(false)
  })

  it('loads preferences saved before the option existed', async () => {
    storage.setItem(
      KEY,
      JSON.stringify({
        is_idle_disconnect_modal_enabled: false,
        is_auto_mute_large_room_enabled: true,
      })
    )
    const store = await loadStore()
    expect(store.is_idle_disconnect_modal_enabled).toBe(false)
    expect(store.join_with_microphone_off).toBe(false)
    expect(store.join_with_camera_off).toBe(false)
  })

  it('persists the switches and loads them back', async () => {
    const store = await loadStore()
    store.join_with_camera_off = true
    await flush()
    expect(JSON.parse(storage.getItem(KEY)!)).toMatchObject({
      join_with_microphone_off: false,
      join_with_camera_off: true,
    })
    const reloaded = await loadStore()
    expect(reloaded.join_with_camera_off).toBe(true)
    expect(reloaded.join_with_microphone_off).toBe(false)
  })
})
