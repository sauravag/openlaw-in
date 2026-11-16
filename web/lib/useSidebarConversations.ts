import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_ENABLED } from './authMode'
import { fetchSidebarPage } from './sidebarConversations'
import type { SidebarConversationData } from './types'

interface SidebarState {
  conversations: readonly SidebarConversationData[]
  nextOffset: number | null
}

const EMPTY: SidebarState = { conversations: [], nextOffset: null }

/**
 * The sidebar's conversations, paged. Page 1 reloads (dropping any further
 * pages) whenever `enabled`, `language`, `showTitles` or `refreshKey` changes;
 * a response that arrives after such a change is ignored, so titles fetched
 * for an old preference never leak in. Mode B only: nothing is asked otherwise,
 * and turning `enabled` off (sign-out) clears the list at once.
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
  const [state, setState] = useState<SidebarState>(EMPTY)
  // Reset during render (React's sanctioned way to react to changed inputs):
  // entries, and any titles, from the previous inputs never show for a frame.
  const inputsKey = JSON.stringify([enabled, language, showTitles, refreshKey])
  const [seenKey, setSeenKey] = useState(inputsKey)
  if (seenKey !== inputsKey) {
    setSeenKey(inputsKey)
    setState(EMPTY)
  }
  // Bumped on every reload; a response from an older generation is dropped.
  const generation = useRef(0)
  const isLoadingMore = useRef(false)
  const nextOffsetRef = useRef<number | null>(null)

  useEffect(() => {
    generation.current += 1
    const mine = generation.current
    isLoadingMore.current = false
    nextOffsetRef.current = null
    if (!AUTH_ENABLED || !enabled) return
    void fetchSidebarPage({ language, offset: 0, includeTitles: showTitles }).then((outcome) => {
      if (generation.current !== mine) return
      if (outcome.kind !== 'page') return
      nextOffsetRef.current = outcome.nextOffset
      setState({ conversations: outcome.conversations, nextOffset: outcome.nextOffset })
    })
  }, [enabled, language, showTitles, refreshKey])

  const loadMore = useCallback(() => {
    const offset = nextOffsetRef.current
    if (!AUTH_ENABLED || !enabled || offset === null || isLoadingMore.current) return
    const mine = generation.current
    isLoadingMore.current = true
    void fetchSidebarPage({ language, offset, includeTitles: showTitles }).then((outcome) => {
      if (generation.current !== mine) return
      isLoadingMore.current = false
      if (outcome.kind !== 'page') return
      nextOffsetRef.current = outcome.nextOffset
      setState((previous) => ({
        conversations: [...previous.conversations, ...outcome.conversations],
        nextOffset: outcome.nextOffset,
      }))
    })
  }, [enabled, language, showTitles])

  return {
    conversations: state.conversations,
    hasMore: state.nextOffset !== null,
    loadMore,
  }
}
