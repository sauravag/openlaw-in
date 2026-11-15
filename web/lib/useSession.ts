import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_ENABLED } from './authMode'

export interface SessionState {
  signedIn: boolean
  email: string | null
}

export interface SessionHandle extends SessionState {
  /** False until the first check has finished, whatever its outcome. */
  isResolved: boolean
  /** Re-ask the server now (unthrottled) and resolve with the answer. */
  refresh: () => Promise<SessionState>
}

/** Anonymous is the conservative answer: it promises the SHORTER retention. */
const ANONYMOUS: SessionState = { signedIn: false, email: null }

/** Focus and visibility re-checks run at most this often. */
export const SESSION_RECHECK_MIN_INTERVAL_MS = 30_000

/** A session request that takes longer than this counts as anonymous, so a
 * hung request can never hold up an upload for more than this long. */
export const SESSION_REQUEST_TIMEOUT_MS = 3_000

/** Accept only the exact `{signedIn, email}` shape; anything else is anonymous. */
function parseSession(body: unknown): SessionState {
  if (typeof body !== 'object' || body === null) return ANONYMOUS
  const { signedIn, email } = body as Record<string, unknown>
  if (signedIn !== true) return ANONYMOUS
  return { signedIn: true, email: typeof email === 'string' ? email : null }
}

/** One request; ANY failure (network, non-2xx, bad body) is anonymous. */
async function fetchSession(signal: AbortSignal): Promise<SessionState> {
  try {
    const response = await fetch('/api/auth/session', { cache: 'no-store', signal })
    if (!response.ok) return ANONYMOUS
    return parseSession(await response.json())
  } catch {
    return ANONYMOUS
  }
}

/**
 * `fetchSession` bounded by `timeoutMs`. The abort signal cancels a request
 * that honours it; the race resolves regardless of whether it does.
 */
async function fetchSessionWithin(timeoutMs: number): Promise<SessionState> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  const timedOut = new Promise<SessionState>((resolve) => {
    timer = setTimeout(() => {
      controller.abort()
      resolve(ANONYMOUS)
    }, timeoutMs)
  })
  try {
    return await Promise.race([fetchSession(controller.signal), timedOut])
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Who is looking at the page. Asks `/api/auth/session` on mount, and again
 * when the tab regains focus or becomes visible (throttled), because the
 * session cookie lapses with the access token while a tab can stay open for
 * hours. Only in Mode B: in Mode A there is nothing to ask and no request,
 * listener or loading state exists.
 *
 * While loading, and on ANY failure, the reader is treated as anonymous: a
 * failed fetch is never read as signed in, so the notice a person sees is
 * never a longer promise than the server will keep.
 */
export function useSession(): SessionHandle {
  const [session, setSession] = useState<SessionState>(ANONYMOUS)
  const [isResolved, setIsResolved] = useState(false)
  const lastCheckAt = useRef(0)
  const latestRequest = useRef(0)

  const refresh = useCallback(async (): Promise<SessionState> => {
    if (!AUTH_ENABLED) return ANONYMOUS
    lastCheckAt.current = Date.now()
    latestRequest.current += 1
    const requestId = latestRequest.current
    const next = await fetchSessionWithin(SESSION_REQUEST_TIMEOUT_MS)
    // A newer request owns the state; an older answer must not overwrite it.
    if (requestId === latestRequest.current) {
      setSession(next)
      setIsResolved(true)
    }
    return next
  }, [])

  useEffect(() => {
    if (!AUTH_ENABLED) return
    void refresh()

    function recheck() {
      if (document.visibilityState === 'hidden') return
      if (Date.now() - lastCheckAt.current < SESSION_RECHECK_MIN_INTERVAL_MS) return
      void refresh()
    }
    window.addEventListener('focus', recheck)
    document.addEventListener('visibilitychange', recheck)
    return () => {
      window.removeEventListener('focus', recheck)
      document.removeEventListener('visibilitychange', recheck)
    }
  }, [refresh])

  return { ...session, isResolved, refresh }
}
