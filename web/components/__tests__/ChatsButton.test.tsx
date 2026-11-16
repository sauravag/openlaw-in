import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ChatHeader from '../ChatHeader'
import ChatsButton from '../ChatsButton'
import { getSidebarStrings } from '@/lib/sidebarStrings'
import { CONVERSATIONS_PANEL_ID } from '@/lib/usePhonePanels'

const sidebarStrings = getSidebarStrings('en')

describe('ChatHeader and the Chats button', () => {
  it('shows the Chats button only when the page passes one in', () => {
    const { rerender } = render(<ChatHeader language="en" isHelpOpen={false} onOpenHelp={vi.fn()} />)
    expect(screen.queryByRole('button', { name: sidebarStrings.conversationsMenuButton })).not.toBeInTheDocument()

    rerender(
      <ChatHeader
        language="en"
        isHelpOpen={false}
        onOpenHelp={vi.fn()}
        conversationsButton={<ChatsButton language="en" isOpen={false} onOpen={vi.fn()} />}
      />,
    )
    const button = screen.getByRole('button', { name: sidebarStrings.conversationsMenuButton })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveAttribute('aria-controls', CONVERSATIONS_PANEL_ID)
    expect(button).toHaveAttribute('aria-haspopup', 'dialog')
  })

  it('reports the panel as expanded, and a click asks to open it', async () => {
    const onOpen = vi.fn()
    render(<ChatsButton language="hi" isOpen onOpen={onOpen} />)
    const button = screen.getByRole('button', { name: getSidebarStrings('hi').conversationsMenuButton })
    expect(button).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(button)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
