import { useState } from 'react'
import { AUTH_ENABLED } from './authMode'
import { storeConversationId } from './thread'

export interface ConversationPointer {
  conversationId: string | null
  setConversationId: (conversationId: string | null) => void
  /** Adopt the conversation the server attached a turn or upload to, and
   * remember it across a reload -- flagged as kept when stored signed in, so
   * a later lapse keeps it (final review H1a). */
  remember: (conversationId: string) => void
}

/** Which conversation this tab is in. Shared by the chat and the documents. */
export function useConversationPointer(isSignedIn: boolean): ConversationPointer {
  const [conversationId, setConversationId] = useState<string | null>(null)

  function remember(id: string): void {
    setConversationId(id)
    storeConversationId(id, AUTH_ENABLED && isSignedIn)
  }

  return { conversationId, setConversationId, remember }
}
