import { useEffect } from 'react'
import { readConversation } from './api'
import { AUTH_ENABLED } from './authMode'
import { attachedFromConversationCard, type AttachedDocument } from './documents'
import type { Language } from './languages'
import {
  forgetStoredConversationId,
  readStoredConversation,
  replayedTurn,
  type ThreadTurn,
} from './thread'
import type { SessionState } from './useSession'

const NOT_FOUND = 404

/** What to do with the stored pointer when the server would not give the
 * conversation back. */
export type RestoreFailureAction = 'forget' | 'keep' | 'sessionEnded'

export interface RestoreFailure {
  authEnabled: boolean
  /** The read's HTTP status; 0 when no response arrived. */
  status: number
  /** Whether a valid session is present now. */
  signedIn: boolean
  storedWhileSignedIn: boolean
}

/**
 * Final review H1a. Mode A is unchanged: any failure forgets the id, because
 * an anonymous conversation expires and its cookie can be cleared.
 *
 * Mode B is stricter, because there a stored conversation may be one the
 * person was promised is kept until they delete it:
 *   - signed in: only a genuine 404 for this owner forgets the id; a network
 *     or server failure keeps it for the next reload.
 *   - not signed in, but stored while signed in: the session lapsed, so the
 *     read went out anonymous and could not see an account-owned row. Keep
 *     the id and tell the person to sign in to see it again.
 *   - not signed in, stored anonymously: the Mode A rule for a 404 (expired,
 *     or the cookie was cleared); any other failure keeps it.
 * Telling a never-signed-in visitor that "your session ended" would be false,
 * which is why the flag stored beside the id decides the middle case.
 */
export function restoreFailureAction(failure: RestoreFailure): RestoreFailureAction {
  if (!failure.authEnabled) return 'forget'
  if (failure.signedIn) return failure.status === NOT_FOUND ? 'forget' : 'keep'
  if (failure.storedWhileSignedIn) return 'sessionEnded'
  return failure.status === NOT_FOUND ? 'forget' : 'keep'
}

export interface RestoreHandlers {
  onRestored: (conversationId: string, turns: ThreadTurn[], documents: AttachedDocument[]) => void
  /** Mode B: the stored conversation needs a sign-in to be seen again. */
  onSessionEnded: () => void
  /** Re-asks the server who is signed in; only called after a failed read. */
  checkSession: () => Promise<SessionState>
}

async function failureAction(status: number, storedWhileSignedIn: boolean, handlers: RestoreHandlers) {
  const signedIn = AUTH_ENABLED ? (await handlers.checkSession()).signedIn : false
  return restoreFailureAction({ authEnabled: AUTH_ENABLED, status, signedIn, storedWhileSignedIn })
}

/**
 * Bring back the conversation this browser tab was in the middle of.
 *
 * The id lives in `sessionStorage` (see `lib/thread.ts` for why not
 * `localStorage`), and the turns themselves come from
 * `GET /conversations/{id}`, never from storage: the authoritative answer to
 * "does this still exist, and is it yours?" belongs to the server. What a
 * refusal means is decided by `restoreFailureAction`.
 */
export function useRestoreConversation(language: Language['code'], handlers: RestoreHandlers) {
  useEffect(() => {
    const stored = readStoredConversation()
    if (stored === null) return

    let isStale = false
    void (async () => {
      const outcome = await readConversation({ conversationId: stored.id, language })
      if (isStale) return
      if (outcome.kind === 'conversation') {
        handlers.onRestored(
          outcome.data.conversation_id,
          outcome.data.turns.map(replayedTurn),
          (outcome.data.documents ?? []).map(attachedFromConversationCard),
        )
        return
      }
      const action = await failureAction(outcome.status, stored.storedWhileSignedIn, handlers)
      if (isStale) return
      if (action === 'forget') forgetStoredConversationId()
      if (action === 'sessionEnded') handlers.onSessionEnded()
    })()
    return () => {
      isStale = true
    }
    // Runs once on mount: a language change must not re-fetch the thread, and
    // the handlers are stable closures over setState calls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
