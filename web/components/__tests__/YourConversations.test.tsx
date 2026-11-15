import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import YourConversations from '../YourConversations'
import { getStrings } from '@/lib/strings'
import type { ConversationListItemData } from '@/lib/types'

vi.mock('@/lib/authMode', () => ({ AUTH_ENABLED: true }))

const items: ConversationListItemData[] = [
  { id: 'conv-1', created_at: '2026-11-14T10:00:00+00:00', updated_at: '2026-11-14T11:00:00+00:00', document_count: 2 },
  { id: 'conv-2', created_at: '2026-11-10T10:00:00+00:00', updated_at: '2026-11-10T10:00:00+00:00', document_count: 1 },
]

describe('YourConversations (final review H1b)', () => {
  it('lists each conversation with its date and document count, metadata only', () => {
    render(<YourConversations conversations={items} currentId={null} language="en" onOpen={() => undefined} />)

    const list = screen.getByRole('list', { name: getStrings('en').yourConversationsHeading })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(list.textContent).toContain('2 documents')
    expect(list.textContent).toContain('1 document')
  })

  it('opens the chosen conversation', async () => {
    const onOpen = vi.fn()
    render(<YourConversations conversations={items} currentId={null} language="en" onOpen={onOpen} />)

    await userEvent.click(screen.getAllByRole('button', { name: getStrings('en').openConversationButton })[1])

    expect(onOpen).toHaveBeenCalledWith('conv-2')
  })

  it('marks the conversation already open instead of offering to open it', () => {
    render(<YourConversations conversations={items} currentId="conv-1" language="en" onOpen={() => undefined} />)

    expect(screen.getAllByRole('button', { name: getStrings('en').openConversationButton })).toHaveLength(1)
    expect(screen.getByText(getStrings('en').currentConversationLabel)).toBeInTheDocument()
  })

  it('renders in Hindi with no English label', () => {
    render(<YourConversations conversations={items} currentId={null} language="hi" onOpen={() => undefined} />)

    expect(screen.getByRole('heading', { name: getStrings('hi').yourConversationsHeading })).toBeInTheDocument()
    expect(screen.queryByText(/Started|documents?/)).not.toBeInTheDocument()
  })

  it('renders nothing when there is nothing to recover', () => {
    const { container } = render(
      <YourConversations conversations={[]} currentId={null} language="en" onOpen={() => undefined} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
