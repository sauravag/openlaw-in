import { useSyncExternalStore } from 'react'

function subscribe(): () => void {
  return () => undefined
}

/** False in the server-rendered HTML (and so for a visitor with JavaScript
 * off), true once React runs in the browser. */
export function useIsHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  )
}
