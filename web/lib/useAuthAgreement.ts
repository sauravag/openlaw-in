import { useEffect, useState } from 'react'
import { readAuthAgreement } from './api'

/**
 * True once the server has PROVEN that this frontend build and the backend
 * disagree on the auth mode (final review H3c). Asked once per page load, in
 * both modes: a Mode A build beside a Mode B backend is exactly the case in
 * which uploads expire under a page built to promise nothing.
 */
export function useAuthMismatch(): boolean {
  const [isMismatched, setIsMismatched] = useState(false)
  useEffect(() => {
    let isStale = false
    void readAuthAgreement().then((agrees) => {
      if (!isStale) setIsMismatched(agrees === false)
    })
    return () => {
      isStale = true
    }
  }, [])
  return isMismatched
}
