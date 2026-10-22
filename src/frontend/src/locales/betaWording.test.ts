import { describe, expect, it } from 'vitest'

// Fusion Meet is not a beta: no translated text may carry a «(beta)» marker.
const locales = import.meta.glob<Record<string, unknown>>('./*/*.json', {
  eager: true,
  import: 'default',
})

const texts = (value: unknown, path: string): Array<[string, string]> => {
  if (typeof value === 'string') return [[path, value]]
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, child]) =>
      texts(child, `${path}.${key}`)
    )
  }
  return []
}

describe('locale texts', () => {
  it('are loaded', () => {
    expect(Object.keys(locales)).toContain('./ru/rooms.json')
  })

  it('carry no beta wording', () => {
    const hits = Object.entries(locales)
      .flatMap(([file, content]) => texts(content, file))
      .filter(([, text]) => /\bbeta\b|бета/i.test(text))
      .map(([path]) => path)
    expect(hits).toEqual([])
  })
})
