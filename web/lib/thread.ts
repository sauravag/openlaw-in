import type {
  AnswerData,
  ConversationTurnCardData,
  Helpline,
  RefusalData,
} from './types'

/**
 * The conversation as this interface holds it: a flat, append-only list of
 * turns, oldest first.
 *
 * Deliberately not a chat transcript of "messages". A turn is one question
 * and the one outcome it produced, which is the unit everything else here
 * cares about: a crisis belongs to a turn, a set of attached documents
 * belongs to a turn, and a refusal is a turn's outcome rather than a separate
 * kind of message. Modelling it the other way (two message rows per turn)
 * made the crisis-ordering rule harder to state and nothing easier.
 *
 * All three functions below return new values; no caller mutates a turn or
 * the list, so React's identity checks stay honest and an older turn can
 * never be rewritten by a later one. That matters beyond tidiness: help
 * already delivered must not be editable by a subsequent failure (Task 6
 * review, M3).
 */

/** Where the current conversation id is kept across a reload. `sessionStorage`,
 * not `localStorage`: a conversation that may quote somebody's eviction notice
 * should not outlive the browser tab on a shared or borrowed phone. */
export const CONVERSATION_STORAGE_KEY = 'openlaw.conversation_id'

/**
 * What came back for one turn.
 *
 * `replay` is a turn restored from `GET /conversations/{id}` after a reload.
 * It is a distinct kind rather than a faked `answer` on purpose: a stored
 * turn carries no `SourceCard`s and no corpus version, so rendering it
 * through `AnswerView` would print an empty "Where this comes from" and a
 * corpus note with a blank version -- law with nothing behind it, which is
 * the one thing this product must never show.
 */
export type TurnOutcome =
  | { kind: 'answer'; data: AnswerData }
  | { kind: 'refused'; data: RefusalData }
  | { kind: 'replay'; data: ConversationTurnCardData }

export interface ThreadTurn {
  /** Stable within the thread; used as the React key and to identify the
   * turn whose crisis block the page has hoisted above everything. */
  id: string
  question: string
  /** The language the turn was asked in -- the fallback for a refusal, which
   * carries no language of its own (`RefusalData`). */
  language: string
  /** Names of the documents read for THIS turn, captured at ask time so a
   * later deletion does not rewrite what the turn says was read. */
  attachedFilenames: readonly string[]
  outcome: TurnOutcome
}

/** Return a new thread with `turn` appended. Never mutates `turns`. */
export function appendTurn(
  turns: readonly ThreadTurn[],
  turn: ThreadTurn,
): ThreadTurn[] {
  return [...turns, turn]
}

/** The newest turn, or `null` for an empty thread. */
export function latestTurn(turns: readonly ThreadTurn[]): ThreadTurn | null {
  return turns.length === 0 ? null : turns[turns.length - 1]
}

/** Everything `CrisisAlert` needs, pulled out of whichever outcome holds it. */
export interface TurnCrisis {
  crisisResponse: string
  helplines: Helpline[]
  language: string
}

/**
 * The crisis attached to `turn`, or `null`.
 *
 * One function for both outcome kinds, so the page can hoist a crisis block
 * above the thread without caring whether the turn was answered or refused --
 * someone in danger who happens to hit a rate limit must see help in exactly
 * the same place as someone who got an answer.
 */
export function turnCrisis(turn: ThreadTurn): TurnCrisis | null {
  if (turn.outcome.kind === 'replay') return null
  const { crisis_response: crisisResponse, helplines } = turn.outcome.data
  if (!crisisResponse) return null
  const language =
    turn.outcome.kind === 'answer' ? turn.outcome.data.language : turn.language
  return { crisisResponse, helplines, language }
}

/** Build a thread turn from a stored one (`GET /conversations/{id}`). */
export function replayedTurn(card: ConversationTurnCardData): ThreadTurn {
  return {
    id: `replay-${card.turn_index}`,
    question: card.question,
    language: card.language,
    // Which documents were read for a past turn is not recorded in the
    // transcript, and inventing a list would be worse than showing none.
    attachedFilenames: [],
    outcome: { kind: 'replay', data: card },
  }
}

/**
 * `sessionStorage`, wrapped.
 *
 * Every accessor can throw outright, not merely return `null`: Safari in
 * private mode and a browser configured to block site data both raise on
 * access. A thrown error here would blank the whole page during render, so
 * each call degrades to "no stored conversation", which is a correct and
 * complete state -- the reader simply starts a fresh thread.
 */
export function readStoredConversationId(): string | null {
  try {
    return window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)
  } catch {
    return null
  }
}

export function storeConversationId(conversationId: string): void {
  try {
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, conversationId)
  } catch {
    // Not being able to remember the id across a reload is a degraded
    // experience, never a failure worth telling the reader about.
  }
}

export function forgetStoredConversationId(): void {
  try {
    window.sessionStorage.removeItem(CONVERSATION_STORAGE_KEY)
  } catch {
    // As above.
  }
}
