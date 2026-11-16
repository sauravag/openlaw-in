import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { AUTH_ENABLED } from './authMode'
import { fetchSidebarPage } from './sidebarConversations'
import type { SidebarConversationData } from './types'

/** Entries tagged with the inputs that produced them, so output from any
 * other inputs can never be returned. */
interface SidebarState {
  key: string
  conversations: readonly SidebarConversationData[]
  nextOffset: number | null
}

const EMPTY: SidebarState = { key: '', conversations: [], nextOffset: null }

/** The entries to show: only those fetched for the current inputs. */
export function selectVisible(state: SidebarState, key: string): SidebarState {
  return state.key === key ? state : EMPTY
}

/**
 * The sidebar's conversations, paged. Page 1 reloads (dropping any further
 * pages) whenever `enabled`, `language`, `showTitles` or `refreshKey` changes.
 * Entries are tagged with the inputs that fetched them and only returned while
 * those are still the current inputs, so titles fetched for an old preference
 * are never shown, even by a response that lands between a render and its
 * effects. Mode B only: nothing is asked otherwise, and turning `enabled` off
 * (sign-out) clears the list at once.
 *
 * A failed fetch is shown as no entries: the sidebar is a convenience, never
 * the only way to reach help.
 */
export function useSidebarConversations(params: {
  enabled: boolean
  language: string
  showTitles: boolean
  refreshKey: string
}): {
  conversations: readonly SidebarConversationData[]
  hasMore: boolean
  loadMore: () => void
} {
  const { enabled, language, showTitles, refreshKey } = params
  const inputsKey = JSON.stringify([enabled, language, showTitles, refreshKey])
  const [state, setState] = useState<SidebarState>(EMPTY)
  // Reset during render (React's sanctioned way to react to changed inputs) so
  // a returning key can never resurface entries from an earlier session.
  const [seenKey, setSeenKey] = useState(inputsKey)
  if (seenKey !== inputsKey) {
    setSeenKey(inputsKey)
    setState(EMPTY)
  }
  const visible = selectVisible(state, inputsKey)

  // Bumped on every reload; a response from an older generation is dropped.
  const generation = useRef(0)
  const isLoadingMore = useRef(false)
  // The offset last asked for. `loadMore` closes over the rendered offset, so a
  // call between a response and the re-render must not ask for the same page.
  const lastRequestedOffset = useRef<number | null>(null)
  // The latest committed inputs. A layout effect, not a passive one: it runs
  // inside the commit, before any fetch can resolve or a stale callback fire.
  const latestKey = useRef(inputsKey)
  useLayoutEffect(() => {
    latestKey.current = inputsKey
  }, [inputsKey])

  useEffect(() => {
    generation.current += 1
    const mine = generation.current
    isLoadingMore.current = false
    lastRequestedOffset.current = null
    if (!AUTH_ENABLED || !enabled) return
    void fetchSidebarPage({ language, offset: 0, includeTitles: showTitles }).then((outcome) => {
      if (generation.current !== mine || outcome.kind !== 'page') return
      setState({
        key: inputsKey,
        conversations: outcome.conversations,
        nextOffset: outcome.nextOffset,
      })
    })
  }, [enabled, language, showTitles, inputsKey])

  const offset = visible.nextOffset
  const loadMore = useCallback(() => {
    // A callback from before the inputs changed must not fire a request for
    // the old preference (e.g. titled after titles were switched off).
    if (latestKey.current !== inputsKey) return
    if (!AUTH_ENABLED || !enabled || offset === null || isLoadingMore.current) return
    if (lastRequestedOffset.current === offset) return
    const mine = generation.current
    isLoadingMore.current = true
    lastRequestedOffset.current = offset
    void fetchSidebarPage({ language, offset, includeTitles: showTitles }).then((outcome) => {
      if (generation.current !== mine) return
      isLoadingMore.current = false
      if (outcome.kind !== 'page') {
        lastRequestedOffset.current = null // a failed page may be retried
        return
      }
      setState((previous) =>
        previous.key !== inputsKey
          ? previous
          : {
              key: inputsKey,
              conversations: [...previous.conversations, ...outcome.conversations],
              nextOffset: outcome.nextOffset,
            },
      )
    })
  }, [enabled, language, showTitles, inputsKey, offset])

  return {
    conversations: visible.conversations,
    hasMore: visible.nextOffset !== null,
    loadMore,
  }
}
