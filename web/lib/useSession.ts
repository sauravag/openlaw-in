import { useEffect, useState } from 'react'
import { AUTH_ENABLED } from './authMode'

export interface SessionState {
  signedIn: boolean
  email: string | null
}

/** Anonymous is the conservative answer: it promises the SHORTER retention. */
const ANONYMOUS: SessionState = { signedIn: false, email: null }

/** Accept only the exact `{signedIn, email}` shape; anything else is anonymous. */
function parseSession(body: unknown): SessionState {
  if (typeof body !== 'object' || body === null) return ANONYMOUS
  const { signedIn, email } = body as Record<string, unknown>
  if (signedIn !== true) return ANONYMOUS
  return { signedIn: true, email: typeof email === 'string' ? email : null }
}

/**
 * Who is looking at the page. Fetches `/api/auth/session` once on mount, and
 * only in Mode B (in Mode A there is nothing to ask and no request is made).
 *
 * While loading, and on ANY failure (network, non-2xx, bad body), the reader
 * is treated as anonymous: a failed fetch is never read as signed in, so the
 * notice a person sees is never a longer promise than the server will keep.
 */
export function useSession(): SessionState {
  const [session, setSession] = useState<SessionState>(ANONYMOUS)

  useEffect(() => {
    if (!AUTH_ENABLED) return
    let isStale = false
    void (async () => {
      try {
        const response = await fetch('/api/auth/session', { cache: 'no-store' })
        if (!response.ok) return
        const next = parseSession(await response.json())
        if (!isStale) setSession(next)
      } catch {
        // Stay anonymous: the conservative promise. Nothing to tell the reader.
      }
    })()
    return () => {
      isStale = true
    }
  }, [])

  return session
}
