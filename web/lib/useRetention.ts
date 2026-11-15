import { useEffect } from 'react'
import { AUTH_ENABLED } from './authMode'
import type { AttachedDocument, Retention } from './documents'
import type { SessionState } from './useSession'

interface RetentionSession {
  signedIn: boolean
  refresh: () => Promise<SessionState>
}

/**
 * Which retention promise the page makes about uploaded documents.
 *
 * Ground truth wins, in EVERY mode (final review H3b): a document that
 * carries an expiry never sits under a "kept" banner, nor under no banner at
 * all in a build without accounts, whatever the (possibly stale) session or
 * the build flag says. `AUTH_ENABLED` is referenced directly so Mode A's
 * build folds the rest away.
 *
 * When the page believes "signed in" but the server stored something that
 * expires, the session is re-checked to find out which of the two is wrong.
 */
export function useRetention(
  documents: readonly AttachedDocument[],
  session: RetentionSession,
): Retention {
  const hasExpiringDocument = documents.some((document) => document.expiresAt !== null)
  const { signedIn, refresh } = session
  useEffect(() => {
    if (AUTH_ENABLED && signedIn && hasExpiringDocument) void refresh()
  }, [signedIn, hasExpiringDocument, refresh])

  if (hasExpiringDocument) return 'expires'
  if (!AUTH_ENABLED) return 'none'
  return signedIn ? 'kept' : 'expires'
}
