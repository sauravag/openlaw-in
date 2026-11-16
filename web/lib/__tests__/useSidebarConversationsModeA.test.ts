import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { fetchSidebarPage } from '../sidebarConversations'
import { useSidebarConversations } from '../useSidebarConversations'

vi.mock('@/lib/authMode', () => ({ AUTH_ENABLED: false }))
vi.mock('../sidebarConversations', () => ({ fetchSidebarPage: vi.fn() }))

describe('useSidebarConversations in Mode A', () => {
  it('never fetches even when enabled', async () => {
    const { result } = renderHook(() =>
      useSidebarConversations({ enabled: true, language: 'en', showTitles: true, refreshKey: 'k' }),
    )
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchSidebarPage).not.toHaveBeenCalled()
    expect(result.current.conversations).toEqual([])
  })
})
