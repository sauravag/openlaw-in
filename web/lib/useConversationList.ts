import { useEffect, useState } from 'react'
import { listConversations } from './api'
import { AUTH_ENABLED } from './authMode'
import type { ConversationListItemData } from './types'

/**
 * The signed-in owner's conversations (final review H1b), refetched when the
 * person signs in and whenever the current conversation changes (a new one
 * was just started). Mode B and signed in only: nothing is asked otherwise.
 *
 * A failed list is shown as no list. It is a convenience for finding a
 * conversation again, never the only way to reach help, and the helplines on
 * the page do not depend on it.
 */
export function useConversationList(
  signedIn: boolean,
  language: string,
  currentConversationId: string | null,
): readonly ConversationListItemData[] {
  const [items, setItems] = useState<readonly ConversationListItemData[]>([])

  useEffect(() => {
    if (!AUTH_ENABLED || !signedIn) {
      setItems([])
      return
    }
    let isStale = false
    void listConversations({ language }).then((outcome) => {
      if (isStale) return
      setItems(outcome.kind === 'list' ? outcome.data.conversations : [])
    })
    return () => {
      isStale = true
    }
    // `language` only picks the refusal's language; a change must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, currentConversationId])

  return items
}
