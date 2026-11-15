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

describe('root viewport (I1)', () => {
  it('lets the soft keyboard resize the layout, so the pinned composer and dvh shrink with it', () => {
    expect(viewport.interactiveWidget).toBe('resizes-content')
    expect(viewport.width).toBe('device-width')
    expect(viewport.initialScale).toBe(1)
  })
})
