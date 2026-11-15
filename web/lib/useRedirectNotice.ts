import { useEffect, useState } from 'react'
import { AUTH_ENABLED } from './authMode'

/** The only redirect outcomes this page will explain. Nothing else renders. */
export type RedirectNotice =
  | { kind: 'migrationFailed' }
  | { kind: 'accountDeleted'; identityRemoved: boolean }

const FLAG_PARAMS = ['migration', 'account', 'identity'] as const

/** Map the exact known flag values to a notice; an unknown value is `null`. */
function noticeFromParams(params: URLSearchParams): RedirectNotice | null {
  if (params.get('migration') === 'failed') return { kind: 'migrationFailed' }
  if (params.get('account') !== 'deleted') return null
  const identity = params.get('identity')
  if (identity === 'removed') return { kind: 'accountDeleted', identityRemoved: true }
  if (identity === 'remains') return { kind: 'accountDeleted', identityRemoved: false }
  return null
}

/**
 * Read the post-redirect flags (`?migration=failed`,
 * `?account=deleted&identity=removed|remains`) once on mount, then strip them
 * from the URL so a refresh or a shared link does not repeat the notice.
 *
 * Returns the notice KIND, never a query value, so nothing a link put in the
 * URL can reach the DOM. Mode A returns null: there is no account to talk about.
 * Must be called before any hook that rewrites the query string.
 */
export function useRedirectNotice(): RedirectNotice | null {
  const [notice, setNotice] = useState<RedirectNotice | null>(null)

  useEffect(() => {
    if (!AUTH_ENABLED) return
    const params = new URLSearchParams(window.location.search)
    const found = noticeFromParams(params)
    const hadFlags = FLAG_PARAMS.some((name) => params.has(name))
    if (found) setNotice(found)
    if (!hadFlags) return
    const remaining = new URLSearchParams(params)
    FLAG_PARAMS.forEach((name) => remaining.delete(name))
    const query = remaining.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`)
  }, [])

  return notice
}
