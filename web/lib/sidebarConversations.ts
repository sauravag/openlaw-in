import { CONVERSATIONS_ENDPOINT, isConversationListItem } from './api'
import type { SidebarConversationData } from './types'

/** Abort a stalled sidebar request instead of leaving it pending forever. */
const REQUEST_TIMEOUT_MS = 30_000

export type SidebarPageOutcome =
  | { kind: 'page'; conversations: SidebarConversationData[]; nextOffset: number | null }
  | { kind: 'failed' }

const FAILED: SidebarPageOutcome = { kind: 'failed' }

function isSidebarItem(value: unknown): value is SidebarConversationData {
  if (!isConversationListItem(value)) return false
  const title = (value as { title?: unknown }).title
  return title === undefined || title === null || typeof title === 'string'
}

/**
 * One page of the signed-in owner's conversations for the sidebar. Titles are
 * asked for only when `includeTitles` is set, so the default request carries
 * no opt-in. **Total: never throws**; any failure is `{ kind: 'failed' }`.
 * Kept out of `lib/api.ts` so the titled request stays out of the Mode A bundle.
 */
export async function fetchSidebarPage(params: {
  language: string
  offset: number
  includeTitles: boolean
}): Promise<SidebarPageOutcome> {
  const titles = params.includeTitles ? '&include_titles=true' : ''
  const url =
    `${CONVERSATIONS_ENDPOINT}?language=${encodeURIComponent(params.language)}` +
    `&offset=${params.offset}${titles}`
  try {
    const response = await fetch(url, {
      method: 'GET',
      credentials: 'include',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    })
    if (!response.ok) return FAILED
    const body: unknown = await response.json()
    if (typeof body !== 'object' || body === null) return FAILED
    const { conversations, next_offset: nextOffset } = body as Record<string, unknown>
    if (!Array.isArray(conversations) || !conversations.every(isSidebarItem)) return FAILED
    if (nextOffset !== null && typeof nextOffset !== 'number') return FAILED
    return { kind: 'page', conversations, nextOffset }
  } catch {
    return FAILED
  }
}
