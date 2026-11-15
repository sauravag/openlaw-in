import { useLayoutEffect, type RefObject } from 'react'

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
  useLayoutEffect(() => {
    const textarea = ref.current
    if (textarea === null) return
    textarea.style.height = 'auto'
    const height = Math.min(textarea.scrollHeight, maxHeightPx)
    // jsdom (and a hidden element) reports 0; leave the CSS minimum alone.
    if (height > 0) textarea.style.height = `${height}px`
    textarea.style.overflowY = textarea.scrollHeight > maxHeightPx ? 'auto' : 'hidden'
  }, [ref, value, maxHeightPx])
}
