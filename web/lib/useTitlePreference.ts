import { useCallback, useEffect, useState } from 'react'

const KEY_PREFIX = 'openlaw.sidebar.titles.'
const ON_VALUE = 'on'

interface Resolved {
  /** The normalised email this answer belongs to. */
  email: string
  on: boolean
}

const normalise = (email: string): string => email.trim().toLowerCase()

/** SHA-256 hex, so the stored key never contains the email itself. */
async function storageKeyFor(email: string): Promise<string> {
  const bytes = new TextEncoder().encode(email)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  const hex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${KEY_PREFIX}${hex}`
}

function readOn(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === ON_VALUE
  } catch {
    return false
  }
}

function writeOn(key: string, on: boolean): void {
  try {
    if (on) window.localStorage.setItem(key, ON_VALUE)
    else window.localStorage.removeItem(key) // "off" leaves no trace
  } catch {
    // Storage blocked: the choice still holds for this render, just not across visits.
  }
}

/**
 * Whether this person chose to see conversation titles on this device.
 * Off by default, when signed out, or whenever storage is unavailable.
 */
export function useTitlePreference(email: string | null): {
  showTitles: boolean
  setShowTitles: (on: boolean) => void
} {
  const person = email === null ? null : normalise(email)
  const [resolved, setResolved] = useState<Resolved | null>(null)

  useEffect(() => {
    if (person === null) return
    let isStale = false
    storageKeyFor(person)
      .then((key) => {
        if (!isStale) setResolved({ email: person, on: readOn(key) })
      })
      .catch(() => {
        // No usable crypto: stay off.
      })
    return () => {
      isStale = true
    }
  }, [person])

  const setShowTitles = useCallback(
    (on: boolean) => {
      if (person === null) return
      setResolved({ email: person, on })
      storageKeyFor(person)
        .then((key) => writeOn(key, on))
        .catch(() => {
          // No usable crypto: the choice holds in memory only.
        })
    },
    [person],
  )

  const showTitles = person !== null && resolved?.email === person && resolved.on
  return { showTitles, setShowTitles }
}
