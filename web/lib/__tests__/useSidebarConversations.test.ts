import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchSidebarPage } from '../sidebarConversations'
import { useSidebarConversations } from '../useSidebarConversations'

vi.mock('@/lib/authMode', () => ({ AUTH_ENABLED: true }))
vi.mock('../sidebarConversations', () => ({ fetchSidebarPage: vi.fn() }))

const fetchMock = vi.mocked(fetchSidebarPage)
type Outcome = Awaited<ReturnType<typeof fetchSidebarPage>>

function item(id: string, title?: string | null) {
  const base = {
    id,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    document_count: 0,
  }
  return title === undefined ? base : { ...base, title }
}

function page(ids: string[], nextOffset: number | null, title?: string | null): Outcome {
  return { kind: 'page', conversations: ids.map((id) => item(id, title)), nextOffset }
}

function deferred() {
  let resolve!: (outcome: Outcome) => void
  const promise = new Promise<Outcome>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

const BASE = { enabled: true, language: 'en', showTitles: false, refreshKey: 'k1' }

beforeEach(() => {
  fetchMock.mockReset()
})

describe('useSidebarConversations', () => {
  it('does not fetch when disabled and returns an empty list', async () => {
    const { result } = renderHook(() => useSidebarConversations({ ...BASE, enabled: false }))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(result.current.conversations).toEqual([])
    expect(result.current.hasMore).toBe(false)
  })

  it('refetches page 1 when refreshKey changes and drops loaded pages', async () => {
    fetchMock.mockResolvedValueOnce(page(['a'], 1)).mockResolvedValueOnce(page(['b'], 2))
    const { result, rerender } = renderHook((p) => useSidebarConversations(p), {
      initialProps: BASE,
    })
    await waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(['a']))
    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(['a', 'b']))

    fetchMock.mockResolvedValueOnce(page(['z'], null))
    rerender({ ...BASE, refreshKey: 'k2' })
    await waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(['z']))
    expect(fetchMock).toHaveBeenLastCalledWith({
      language: 'en',
      offset: 0,
      includeTitles: false,
    })
  })

  it('loadMore appends the next page and hasMore follows nextOffset', async () => {
    fetchMock.mockResolvedValueOnce(page(['a'], 20)).mockResolvedValueOnce(page(['b'], null))
    const { result } = renderHook(() => useSidebarConversations(BASE))
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(['a', 'b']))
    expect(fetchMock).toHaveBeenLastCalledWith({ language: 'en', offset: 20, includeTitles: false })
    expect(result.current.hasMore).toBe(false)
  })

  it('loadMore is a no-op while a load is in flight', async () => {
    const pending = deferred()
    fetchMock.mockResolvedValueOnce(page(['a'], 1)).mockReturnValueOnce(pending.promise)
    const { result } = renderHook(() => useSidebarConversations(BASE))
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => result.current.loadMore())
    act(() => result.current.loadMore())
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await act(async () => pending.resolve(page(['b'], null)))
  })

  it('ignores a loadMore response that resolves after a reload', async () => {
    const pending = deferred()
    fetchMock.mockResolvedValueOnce(page(['a'], 1)).mockReturnValueOnce(pending.promise)
    const { result, rerender } = renderHook((p) => useSidebarConversations(p), {
      initialProps: BASE,
    })
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => result.current.loadMore())
    fetchMock.mockResolvedValueOnce(page(['z'], null))
    rerender({ ...BASE, refreshKey: 'k2' })
    await waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(['z']))
    await act(async () => pending.resolve(page(['stale'], 5)))
    expect(result.current.conversations.map((c) => c.id)).toEqual(['z'])
    expect(result.current.hasMore).toBe(false)
  })

  it('turning titles off clears titles at once and refetches without them', async () => {
    fetchMock.mockResolvedValueOnce(page(['a'], null, 'Title A'))
    const { result, rerender } = renderHook((p) => useSidebarConversations(p), {
      initialProps: { ...BASE, showTitles: true },
    })
    await waitFor(() => expect(result.current.conversations[0]?.title).toBe('Title A'))

    const pending = deferred()
    fetchMock.mockReturnValueOnce(pending.promise)
    rerender({ ...BASE, showTitles: false })
    expect(result.current.conversations.some((c) => 'title' in c && c.title)).toBe(false)
    expect(fetchMock).toHaveBeenLastCalledWith({ language: 'en', offset: 0, includeTitles: false })
    await act(async () => pending.resolve(page(['a'], null)))
    expect(result.current.conversations.map((c) => c.id)).toEqual(['a'])
  })

  it('ignores a response that resolves after showTitles changed', async () => {
    const titled = deferred()
    fetchMock.mockReturnValueOnce(titled.promise).mockResolvedValueOnce(page(['a'], null))
    const { result, rerender } = renderHook((p) => useSidebarConversations(p), {
      initialProps: { ...BASE, showTitles: true },
    })
    rerender({ ...BASE, showTitles: false })
    await waitFor(() => expect(result.current.conversations.map((c) => c.id)).toEqual(['a']))
    await act(async () => titled.resolve(page(['a'], null, 'Leaked title')))
    expect(result.current.conversations).toEqual([item('a')])
  })

  it('clears the list when it becomes disabled', async () => {
    fetchMock.mockResolvedValueOnce(page(['a'], 1, 'T'))
    const { result, rerender } = renderHook((p) => useSidebarConversations(p), {
      initialProps: { ...BASE, showTitles: true },
    })
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
    rerender({ ...BASE, showTitles: true, enabled: false })
    expect(result.current.conversations).toEqual([])
    expect(result.current.hasMore).toBe(false)
  })

  it('a failed fetch shows no entries', async () => {
    fetchMock.mockResolvedValueOnce({ kind: 'failed' })
    const { result } = renderHook(() => useSidebarConversations(BASE))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current.conversations).toEqual([])
    expect(result.current.hasMore).toBe(false)
  })
})
