import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTitlePreference } from '../useTitlePreference'

const PERSON_A = 'one@example.com'
const PERSON_B = 'two@example.com'

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function turnOn(email: string) {
  const { result, unmount } = renderHook(() => useTitlePreference(email))
  act(() => result.current.setShowTitles(true))
  await waitFor(() => expect(Object.keys(window.localStorage).length).toBe(1))
  unmount()
}

describe('useTitlePreference', () => {
  it('defaults to off for a person who never turned it on', async () => {
    const { result } = renderHook(() => useTitlePreference(PERSON_A))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current.showTitles).toBe(false)
  })

  it('remembers on for the same person after a remount', async () => {
    await turnOn(PERSON_A)
    const { result } = renderHook(() => useTitlePreference(PERSON_A))
    await waitFor(() => expect(result.current.showTitles).toBe(true))
  })

  it('treats email case and surrounding spaces as the same person', async () => {
    await turnOn(PERSON_A)
    const { result } = renderHook(() => useTitlePreference('  One@Example.COM '))
    await waitFor(() => expect(result.current.showTitles).toBe(true))
  })

  it('a different person on the same device starts off', async () => {
    await turnOn(PERSON_A)
    const { result } = renderHook(() => useTitlePreference(PERSON_B))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current.showTitles).toBe(false)
  })

  it('never stores the email itself', async () => {
    await turnOn(PERSON_A)
    for (const key of Object.keys(window.localStorage)) {
      expect(key).not.toContain(PERSON_A)
      expect(window.localStorage.getItem(key)).not.toContain(PERSON_A)
    }
  })

  it('leaves no trace once turned back off', async () => {
    const { result } = renderHook(() => useTitlePreference(PERSON_A))
    act(() => result.current.setShowTitles(true))
    await waitFor(() => expect(window.localStorage.length).toBe(1))
    act(() => result.current.setShowTitles(false))
    await waitFor(() => expect(window.localStorage.length).toBe(0))
    expect(result.current.showTitles).toBe(false)
  })

  it('falls back to off when storage throws', async () => {
    await turnOn(PERSON_A)
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const { result } = renderHook(() => useTitlePreference(PERSON_A))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current.showTitles).toBe(false)

    act(() => result.current.setShowTitles(true))
    await waitFor(() => expect(result.current.showTitles).toBe(true))
  })

  it('is off when signed out', async () => {
    await turnOn(PERSON_A)
    const { result } = renderHook(() => useTitlePreference(null))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current.showTitles).toBe(false)
  })
})
