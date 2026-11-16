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
 * The sidebar's conversations, paged.
 *
 * Two kinds of input change, handled differently:
 * - `enabled` or `showTitles` (the privacy inputs): the list is emptied at
 *   once, during render, and reloaded. Entries are tagged with these inputs
 *   and only returned while they are still current, so titles fetched for an
 *   old preference are never shown, even by a response that lands between a
 *   render and its effects; turning `enabled` off (sign-out) clears the list.
 * - `refreshKey`: page 1 is reloaded but the entries on screen stay until it
 *   replaces them (dropping any further pages); a failed reload keeps them.
 *
 * `language` only picks the language of a refusal, so it reloads nothing.
 * Mode B only: nothing is asked otherwise. A failed first fetch is shown as no
 * entries: the sidebar is a convenience, never the only way to reach help.
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
  const privacyKey = JSON.stringify([enabled, showTitles])
  const [state, setState] = useState<SidebarState>(EMPTY)
  // Reset during render (React's sanctioned way to react to changed inputs) so
  // a returning key can never resurface entries from an earlier session.
  const [seenKey, setSeenKey] = useState(privacyKey)
  if (seenKey !== privacyKey) {
    setSeenKey(privacyKey)
    setState(EMPTY)
  }
  const visible = selectVisible(state, privacyKey)

  // Bumped on every reload; a response from an older generation is dropped.
  const generation = useRef(0)
  // Page 1 or a further page is on its way: `loadMore` waits for it, so a
  // further page never lands on top of a page 1 that replaced its list.
  const isFetching = useRef(false)
  // The offset last asked for. `loadMore` closes over the rendered offset, so a
  // call between a response and the re-render must not ask for the same page.
  const lastRequestedOffset = useRef<number | null>(null)
  // The latest committed privacy inputs, and the language to ask in. Layout
  // effects, not passive ones: they run inside the commit, before any fetch
  // can resolve or a stale callback fire.
  const latestKey = useRef(privacyKey)
  const latestLanguage = useRef(language)
  useLayoutEffect(() => {
    latestKey.current = privacyKey
    latestLanguage.current = language
  }, [privacyKey, language])

  useEffect(() => {
    generation.current += 1
    const mine = generation.current
    isFetching.current = false
    lastRequestedOffset.current = null
    if (!AUTH_ENABLED || !enabled) return
    isFetching.current = true
    const request = { language: latestLanguage.current, offset: 0, includeTitles: showTitles }
    void fetchSidebarPage(request).then((outcome) => {
      if (generation.current !== mine) return
      isFetching.current = false
      if (outcome.kind !== 'page') return // keep what is on screen
      setState({ key: privacyKey, conversations: outcome.conversations, nextOffset: outcome.nextOffset })
    })
  }, [enabled, showTitles, privacyKey, refreshKey])

  const offset = visible.nextOffset
  const loadMore = useCallback(() => {
    // A callback from before the inputs changed must not fire a request for
    // the old preference (e.g. titled after titles were switched off).
    if (latestKey.current !== privacyKey) return
    if (!AUTH_ENABLED || !enabled || offset === null || isFetching.current) return
    if (lastRequestedOffset.current === offset) return
    const mine = generation.current
    isFetching.current = true
    lastRequestedOffset.current = offset
    const request = { language: latestLanguage.current, offset, includeTitles: showTitles }
    void fetchSidebarPage(request).then((outcome) => {
      if (generation.current !== mine) return
      isFetching.current = false
      if (outcome.kind !== 'page') {
        lastRequestedOffset.current = null // a failed page may be retried
        return
      }
      setState((previous) =>
        previous.key !== privacyKey
          ? previous
          : {
              key: privacyKey,
              conversations: [...previous.conversations, ...outcome.conversations],
              nextOffset: outcome.nextOffset,
            },
      )
    })
  }, [enabled, showTitles, privacyKey, offset])

  return {
    conversations: visible.conversations,
    hasMore: visible.nextOffset !== null,
    loadMore,
  }
}
