import type { AnswerData, RefusalData } from './types'

/**
 * Talks to the `/ask` endpoint (`src/openlaw/api/routes.py`) -- and only
 * that endpoint. There is deliberately no client-side path that calls
 * `answer_question`/`answer_in_language` directly (there is no such thing in
 * a browser -- those are Python functions), and no second HTTP endpoint:
 * every question this interface asks goes through `/ask`, which is the one
 * place `persist_answer_events` runs. Going around it would either
 * double-write a turn's safety events or drop them silently.
 *
 * Requests go to `/api/openlaw/ask`, a same-origin path that `next.config.ts`
 * rewrites (server-side, before it reaches the browser) to the real API.
 * That is what lets `credentials: 'include'` work with a plain, un-configured
 * backend: the anonymous-quota cookie (`openlaw_anon_id`, set by
 * `create_app`'s middleware) is set on a same-origin response, so no CORS
 * setup is needed for it to survive across requests -- the anonymous path
 * must work with no account, and a cookie the browser refuses to keep would
 * silently break that.
 */
const ASK_ENDPOINT = '/api/openlaw/ask'

export interface AskParams {
  question: string
  /** Never omitted: an explicit choice, not left for the server to guess.
   * See `lib/languages.ts` -- this is always `'en'` or `'hi'`. */
  language: string
  state?: string | null
}

export type AskOutcome =
  | { kind: 'answer'; data: AnswerData }
  | { kind: 'refused'; status: number; data: RefusalData }

/** Raised for anything that is not a well-formed `/ask` response: the
 * network failing outright, or a status code this client does not model. */
export class AskError extends Error {}

function extractDetailMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string' && detail.length > 0) {
      return detail
    }
  }
  return `Request failed with status ${status}.`
}

/**
 * Ask one question and return either a verified answer or a structured
 * refusal. Never throws for a 429 -- that is `QuotaExceeded`, an expected,
 * fully-typed outcome the caller must render (quota state, the escalation
 * footer, and crisis help when present), not an error to swallow.
 */
export async function askQuestion(params: AskParams): Promise<AskOutcome> {
  let response: Response
  try {
    response = await fetch(ASK_ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: params.question,
        language: params.language,
        state: params.state ?? null,
      }),
    })
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Network request failed.'
    throw new AskError(`Could not reach the server: ${message}`)
  }

  const body: unknown = await response.json().catch(() => null)

  if (response.status === 429) {
    if (!body) {
      throw new AskError('The server refused the request but sent no details.')
    }
    return { kind: 'refused', status: response.status, data: body as RefusalData }
  }

  if (!response.ok) {
    throw new AskError(extractDetailMessage(body, response.status))
  }

  return { kind: 'answer', data: body as AnswerData }
}
