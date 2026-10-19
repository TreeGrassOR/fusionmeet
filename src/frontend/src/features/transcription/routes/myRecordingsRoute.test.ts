import { describe, expect, it } from 'vitest'
import { routes } from '@/routes'

describe('my recordings route', () => {
  it('is not captured by the room route', () => {
    const roomPath = routes.room.path as RegExp
    const myRecordingsPath = routes.myRecordings.path as string

    expect(myRecordingsPath).toBe('/my-recordings')
    expect(roomPath.test(myRecordingsPath)).toBe(false)
    // The reason it is not "/recordings".
    expect(roomPath.test('/recordings')).toBe(true)
  })
})
