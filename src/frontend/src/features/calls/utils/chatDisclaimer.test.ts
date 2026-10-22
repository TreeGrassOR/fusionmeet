import { describe, expect, it } from 'vitest'
import en from '@/locales/en/rooms.json'
import ru from '@/locales/ru/rooms.json'
import fr from '@/locales/fr/rooms.json'
import de from '@/locales/de/rooms.json'
import es from '@/locales/es/rooms.json'
import nl from '@/locales/nl/rooms.json'
import { chatDisclaimerKey } from './chatDisclaimer'

describe('chatDisclaimerKey', () => {
  it('keeps the notice of upstream without the call history', () => {
    expect(chatDisclaimerKey(undefined)).toBe('disclaimer')
    expect(chatDisclaimerKey({ enabled: false })).toBe('disclaimer')
    expect(
      chatDisclaimerKey({ enabled: false, visible_to_room_admins: true })
    ).toBe('disclaimer')
  })

  it('says who reads the history when it keeps the messages', () => {
    expect(
      chatDisclaimerKey({ enabled: true, visible_to_room_admins: true })
    ).toBe('disclaimerSavedWithAdmins')
    // the backend default
    expect(chatDisclaimerKey({ enabled: true })).toBe(
      'disclaimerSavedWithAdmins'
    )
    expect(
      chatDisclaimerKey({ enabled: true, visible_to_room_admins: false })
    ).toBe('disclaimerSaved')
  })

  it('has the three notices in every language of the chat', () => {
    for (const rooms of [en, ru, fr, de, es, nl]) {
      expect(rooms.chat.disclaimer).toBeTruthy()
      expect(rooms.chat.disclaimerSaved).toBeTruthy()
      expect(rooms.chat.disclaimerSavedWithAdmins).toBeTruthy()
    }
    // without the history nothing is kept
    expect(en.chat.disclaimer).toMatch(/deleted at the end of the call/)
    expect(ru.chat.disclaimer).toMatch(/все сообщения удаляются/)
    expect(ru.chat.disclaimerSavedWithAdmins).toMatch(/администратор/)
  })
})
