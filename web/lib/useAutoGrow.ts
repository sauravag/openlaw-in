import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react'

/**
 * Grow a textarea with its content up to `maxHeightPx`, then let it scroll.
 *
 * CSS `field-sizing: content` would do this alone, but Firefox and older
 * Safari/Chrome on the cheap phones this is built for do not support it.
 */
export function useAutoGrow(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  maxHeightPx: number,
): void {
  // The width the box was last fitted at, so a resize re-fits only when the
  // width actually moved since then.
  const fittedWidth = useRef<number | null>(null)

  useLayoutEffect(() => {
    const textarea = ref.current
    if (textarea === null) return
    fitHeight(textarea, maxHeightPx)
    fittedWidth.current = textarea.clientWidth
  }, [ref, value, maxHeightPx])

  // Resizing the window rewraps the text (or placeholder) without changing
  // `value`, so the effect above never re-runs and the box stays pinned at the
  // height measured at the old width: clipped when narrower, too tall when
  // wider. Re-fit on width changes only -- fitting sets the height, and
  // reacting to that would loop.
  useEffect(() => {
    const textarea = ref.current
    if (textarea === null || typeof ResizeObserver !== 'function') return
    const observer = new ResizeObserver(() => {
      const width = textarea.clientWidth
      if (width === fittedWidth.current) return
      fitHeight(textarea, maxHeightPx)
      fittedWidth.current = width
    })
    observer.observe(textarea)
    return () => observer.disconnect()
  }, [ref, maxHeightPx])
}

function fitHeight(textarea: HTMLTextAreaElement, maxHeightPx: number): void {
  textarea.style.height = 'auto'
  const height = Math.min(textarea.scrollHeight, maxHeightPx)
  // jsdom (and a hidden element) reports 0; leave the CSS minimum alone.
  if (height > 0) textarea.style.height = `${height}px`
  textarea.style.overflowY = textarea.scrollHeight > maxHeightPx ? 'auto' : 'hidden'
}
