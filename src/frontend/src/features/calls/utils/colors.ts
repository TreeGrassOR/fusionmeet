/**
 * A stable number from a string (FNV-1a), to pick a colour for an id.
 */
export const hashString = (value: string) => {
  let hash = 0x811c9dc5
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

export const pickIndex = (value: string, count: number) =>
  hashString(value) % count

// Avatar backgrounds under white initials (contrast ≥ 4.5:1), the same in
// both themes
export const AVATAR_COLORS = [
  '#2563EB', // blue.600
  '#4F46E5', // indigo.600
  '#7C3AED', // violet.600
  '#A21CAF', // fuchsia.700
  '#BE185D', // pink.700
  '#DC2626', // red.600
  '#C2410C', // orange.700
  '#B45309', // amber.700
  '#047857', // emerald.700
  '#0F766E', // teal.700
  '#0E7490', // cyan.700
  '#0369A1', // sky.700
] as const

export const avatarColor = (id: string) =>
  AVATAR_COLORS[pickIndex(id, AVATAR_COLORS.length)]
