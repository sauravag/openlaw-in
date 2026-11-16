import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { usePhonePanels } from '../usePhonePanels'

describe('usePhonePanels', () => {
  it('starts with both panels closed', () => {
    const { result } = renderHook(() => usePhonePanels(true))
    expect(result.current.help.isOpen).toBe(false)
    expect(result.current.conversations.isOpen).toBe(false)
  })

  it('opening conversations closes help and vice versa', () => {
    const { result } = renderHook(() => usePhonePanels(true))

    act(() => result.current.help.open())
    expect(result.current.help.isOpen).toBe(true)

    act(() => result.current.conversations.open())
    expect(result.current.conversations.isOpen).toBe(true)
    expect(result.current.help.isOpen).toBe(false)

    act(() => result.current.help.open())
    expect(result.current.help.isOpen).toBe(true)
    expect(result.current.conversations.isOpen).toBe(false)
  })

  it('closing one panel leaves the other as it was', () => {
    const { result } = renderHook(() => usePhonePanels(true))
    act(() => result.current.help.open())

    act(() => result.current.conversations.close())
    expect(result.current.help.isOpen).toBe(true)

    act(() => result.current.help.close())
    expect(result.current.help.isOpen).toBe(false)
  })

  it('closes the conversations panel when the sidebar goes away, so it stays shut on the next sign-in', () => {
    const { result, rerender } = renderHook((available: boolean) => usePhonePanels(available), {
      initialProps: true,
    })
    act(() => result.current.conversations.open())

    rerender(false)
    expect(result.current.conversations.isOpen).toBe(false)

    rerender(true)
    expect(result.current.conversations.isOpen).toBe(false)
  })

  it('leaves the help sheet open when the sidebar goes away', () => {
    const { result, rerender } = renderHook((available: boolean) => usePhonePanels(available), {
      initialProps: true,
    })
    act(() => result.current.help.open())
    rerender(false)
    expect(result.current.help.isOpen).toBe(true)
  })
})
