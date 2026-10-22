import { useEffect, useState } from 'react'

type ScrollHint = {
  'data-overflow-start'?: true
  'data-overflow-end'?: true
}

// below this, the content is considered at the edge
const EDGE_TOLERANCE_PX = 2

/**
 * Data attributes telling whether a horizontally scrolled element has more
 * content past its start or end edge, to show that it scrolls (a fade).
 * `ref` is a callback ref: the element may come after the first render.
 */
export const useScrollHint = <T extends HTMLElement>() => {
  const [element, setElement] = useState<T | null>(null)
  const [hint, setHint] = useState<ScrollHint>({})

  useEffect(() => {
    if (!element) return
    const update = () => {
      const { scrollLeft, scrollWidth, clientWidth } = element
      // scrollLeft is negative from the right in right-to-left pages
      const start = Math.abs(scrollLeft)
      const isStart = start > EDGE_TOLERANCE_PX
      const isEnd = scrollWidth - clientWidth - start > EDGE_TOLERANCE_PX
      setHint((previous) =>
        !!previous['data-overflow-start'] === isStart &&
        !!previous['data-overflow-end'] === isEnd
          ? previous
          : {
              ...(isStart && { 'data-overflow-start': true }),
              ...(isEnd && { 'data-overflow-end': true }),
            }
      )
    }
    update()
    element.addEventListener('scroll', update, { passive: true })
    const observer = new ResizeObserver(update)
    observer.observe(element)
    for (const child of element.children) observer.observe(child)
    return () => {
      element.removeEventListener('scroll', update)
      observer.disconnect()
    }
  }, [element])

  return { ref: setElement, element, hint }
}
