// control characters (below U+0020) have no place in a file name
const withoutControlCharacters = (value: string) =>
  Array.from(value, (char) => (char.charCodeAt(0) < 0x20 ? ' ' : char)).join('')

/**
 * A file name from a title: without the characters file systems refuse.
 */
export const fileNameFromTitle = (title: string) =>
  withoutControlCharacters(title)
    .replace(/[\\/:*?"<>|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
    .trim()
