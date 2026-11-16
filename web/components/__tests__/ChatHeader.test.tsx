import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ChatHeader from '../ChatHeader'
import { CONVERSATIONS_PANEL_ID } from '@/lib/usePhonePanels'
import { getStrings } from '@/lib/strings'

vi.mock('@/lib/authMode', () => ({ AUTH_ENABLED: true }))

const strings = getStrings('en')

describe('ChatHeader', () => {
  it('shows the Chats button only when showConversationsButton', () => {
    const { rerender } = render(<ChatHeader language="en" isHelpOpen={false} onOpenHelp={vi.fn()} />)
    expect(screen.queryByRole('button', { name: strings.conversationsMenuButton })).not.toBeInTheDocument()

    rerender(
      <ChatHeader
        language="en"
        isHelpOpen={false}
        onOpenHelp={vi.fn()}
        showConversationsButton
        isConversationsOpen={false}
        onOpenConversations={vi.fn()}
      />,
    )
    const button = screen.getByRole('button', { name: strings.conversationsMenuButton })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveAttribute('aria-controls', CONVERSATIONS_PANEL_ID)
    expect(button).toHaveAttribute('aria-haspopup', 'dialog')
  })

  it('reports the panel as expanded, and a click asks to open it', async () => {
    const onOpen = vi.fn()
    render(
      <ChatHeader
        language="hi"
        isHelpOpen={false}
        onOpenHelp={vi.fn()}
        showConversationsButton
        isConversationsOpen
        onOpenConversations={onOpen}
      />,
    )
    const button = screen.getByRole('button', { name: getStrings('hi').conversationsMenuButton })
    expect(button).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(button)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
