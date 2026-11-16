import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ConversationSidebar from '@/components/ConversationSidebar'
import type { SidebarConversationData } from '@/lib/types'

const NOW = new Date(2026, 10, 16, 12, 0)
const at = (day: number, hour: number, minute: number): string =>
  new Date(2026, 10, day, hour, minute).toISOString()

const ITEMS: SidebarConversationData[] = [
  { id: 'a', created_at: at(16, 9, 3), updated_at: at(16, 9, 3), document_count: 2, title: 'Rent deposit' },
  { id: 'b', created_at: at(15, 18, 40), updated_at: at(15, 18, 40), document_count: 0, title: null },
]

function renderSidebar(overrides: Partial<React.ComponentProps<typeof ConversationSidebar>> = {}) {
  const props = {
    language: 'en',
    conversations: ITEMS,
    hasMore: false,
    onLoadMore: vi.fn(),
    currentId: null,
    showTitles: false,
    onToggleTitles: vi.fn(),
    onOpen: vi.fn(),
    onNewChat: vi.fn(),
    isBusy: false,
    now: NOW,
    ...overrides,
  }
  render(<ConversationSidebar {...props} />)
  return props
}

describe('ConversationSidebar', () => {
  it('is a nav labelled Your conversations', () => {
    renderSidebar()
    expect(screen.getByRole('navigation', { name: 'Your conversations' })).toBeTruthy()
  })

  it('shows time and document count but no title when titles are off', () => {
    renderSidebar({ showTitles: false })
    expect(screen.getByText('Today, 9:03 · 2 documents')).toBeTruthy()
    expect(screen.getByText('Yesterday, 18:40')).toBeTruthy()
    expect(screen.queryByText('Rent deposit')).toBeNull()
    expect(screen.queryByText('Documents only')).toBeNull()
  })

  it('shows the title when titles are on, and Documents only for a null title', () => {
    renderSidebar({ showTitles: true })
    expect(screen.getByText('Rent deposit')).toBeTruthy()
    expect(screen.getByText('Documents only')).toBeTruthy()
  })

  it('marks the open conversation with aria-current=page and does not offer to open it again', () => {
    const props = renderSidebar({ currentId: 'a' })
    const current = document.querySelector('[aria-current="page"]') as HTMLElement
    expect(current).not.toBeNull()
    expect(current.tagName).not.toBe('BUTTON')
    fireEvent.click(current)
    expect(props.onOpen).not.toHaveBeenCalled()
  })

  it('clicking an entry calls onOpen with its id', () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: /Yesterday, 18:40/ }))
    expect(props.onOpen).toHaveBeenCalledWith('b')
  })

  it('New chat calls onNewChat', () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }))
    expect(props.onNewChat).toHaveBeenCalledTimes(1)
  })

  it('entries and New chat are disabled while busy', () => {
    renderSidebar({ isBusy: true })
    expect((screen.getByRole('button', { name: 'New chat' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: /Yesterday, 18:40/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('Show more appears only when hasMore', () => {
    const { onLoadMore } = renderSidebar({ hasMore: true })
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }))
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })

  it('hides Show more when there is no more', () => {
    renderSidebar({ hasMore: false })
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull()
  })

  it('the titles switch is a labelled checkbox that calls onToggleTitles', () => {
    const props = renderSidebar({ showTitles: false })
    const box = screen.getByRole('checkbox', { name: 'Show titles' })
    fireEvent.click(box)
    expect(props.onToggleTitles).toHaveBeenCalledWith(true)
  })

  it('renders Hindi copy for language hi', () => {
    renderSidebar({ language: 'hi' })
    expect(screen.getByRole('navigation', { name: 'आपकी बातचीत' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'नई बातचीत' })).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: 'शीर्षक दिखाएं' })).toBeTruthy()
  })
})
