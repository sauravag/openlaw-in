import { act, render } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutoGrow } from '../useAutoGrow'

const MAX_HEIGHT_PX = 190

function Box({ value }: { value: string }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useAutoGrow(ref, value, MAX_HEIGHT_PX)
  return <textarea ref={ref} value={value} readOnly />
}

/** jsdom has no layout: let each test say how tall and wide the box is. */
function fakeLayout(textarea: HTMLTextAreaElement, layout: { scrollHeight: number; width: number }) {
  Object.defineProperty(textarea, 'scrollHeight', { configurable: true, get: () => layout.scrollHeight })
  Object.defineProperty(textarea, 'clientWidth', { configurable: true, get: () => layout.width })
}

let observerCallback: ResizeObserverCallback | null = null

beforeEach(() => {
  observerCallback = null
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        observerCallback = callback
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useAutoGrow', () => {
  it('grows the box to fit its content when the text changes', () => {
    const { container, rerender } = render(<Box value="" />)
    const textarea = container.querySelector('textarea')!
    fakeLayout(textarea, { scrollHeight: 80, width: 600 })

    rerender(<Box value="a longer question" />)

    expect(textarea.style.height).toBe('80px')
  })

  it('caps the height and lets the box scroll past the maximum', () => {
    const { container, rerender } = render(<Box value="" />)
    const textarea = container.querySelector('textarea')!
    fakeLayout(textarea, { scrollHeight: 500, width: 600 })

    rerender(<Box value="a very long question" />)

    expect(textarea.style.height).toBe(`${MAX_HEIGHT_PX}px`)
    expect(textarea.style.overflowY).toBe('auto')
  })

  it('re-fits the height when the window is resized and the text rewraps, with no text change', () => {
    // Found on the live app: loaded at 1920px wide, then narrowed to 380px,
    // the placeholder needed 104px but the box stayed pinned at the height
    // measured at the wide width -- clipped, because nothing re-measured.
    const { container, rerender } = render(<Box value="" />)
    const textarea = container.querySelector('textarea')!
    const layout = { scrollHeight: 52, width: 687 }
    fakeLayout(textarea, layout)
    rerender(<Box value="question" />)
    expect(textarea.style.height).toBe('52px')

    layout.width = 225
    layout.scrollHeight = 104
    act(() => observerCallback?.([], {} as ResizeObserver))

    expect(textarea.style.height).toBe('104px')
  })

  it('ignores height-only resize notifications, so setting the height cannot loop', () => {
    const { container, rerender } = render(<Box value="" />)
    const textarea = container.querySelector('textarea')!
    const layout = { scrollHeight: 52, width: 687 }
    fakeLayout(textarea, layout)
    rerender(<Box value="question" />)

    layout.scrollHeight = 999 // would change the height if it re-measured
    act(() => observerCallback?.([], {} as ResizeObserver))

    expect(textarea.style.height).toBe('52px')
  })
})
