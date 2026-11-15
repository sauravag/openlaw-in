import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ChatLayout from '../ChatLayout'
import { viewport } from '@/app/layout'

describe('ChatLayout', () => {
  it('falls back to 100vh where dvh is unsupported (older Android WebViews, M3)', () => {
    const { container } = render(
      <ChatLayout
        header={null}
        messages={null}
        composer={null}
        sidePane={null}
        isSheetOpen={false}
        onCloseSheet={vi.fn()}
      />,
    )
    const classes = (container.firstElementChild as HTMLElement).className.split(/\s+/)
    expect(classes).toContain('h-screen')
    expect(classes).toContain('supports-[height:100dvh]:h-dvh')
    expect(classes).not.toContain('h-dvh')
  })
})

describe('ChatLayout containing block', () => {
  it('positions the root, so sr-only text cannot grow the document and let the reply scroll move the whole page', () => {
    // Found on the live app at 390px: `sr-only` is `position: absolute`. With
    // no positioned ancestor it anchored to the page, made the document
    // 3309px tall inside an 844px viewport, and the reply's scrollIntoView
    // then scrolled the window -- pushing the header and the helpline strip
    // off-screen. jsdom has no layout, so this pins the class, not the pixels.
    const { container } = render(
      <ChatLayout
        header={null}
        messages={null}
        composer={null}
        sidePane={null}
        isSheetOpen={false}
        onCloseSheet={vi.fn()}
      />,
    )
    const classes = (container.firstElementChild as HTMLElement).className.split(/\s+/)
    expect(classes).toContain('relative')
  })
})

describe('root viewport (I1)', () => {
  it('lets the soft keyboard resize the layout, so the pinned composer and dvh shrink with it', () => {
    expect(viewport.interactiveWidget).toBe('resizes-content')
    expect(viewport.width).toBe('device-width')
    expect(viewport.initialScale).toBe(1)
  })
})
