import { getFallbackHelplineSet } from "./fallbackHelplines";
import { getStrings } from "./strings";
import type { AnswerData, Helpline, RefusalData, SourceCard } from "./types";

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
const ASK_ENDPOINT = "/api/openlaw/ask";

//: A stalled request on a flaky mobile connection must not pin the UI at
//: "Asking…" forever (Task 6 review, H4) -- 30s is generous for a legal-answer
//: round trip (retrieval + generation) while still bounding the wait.
const REQUEST_TIMEOUT_MS = 30_000;

//: `askQuestion` never has a real HTTP status for a network failure or a
//: timeout (`fetch` throws before one exists) -- `0` is the conventional
//: "no response was ever received" sentinel, distinct from any real status.
const NO_RESPONSE_STATUS = 0;

export interface AskParams {
  question: string;
  /** Never omitted: an explicit choice, not left for the server to guess.
   * See `lib/languages.ts` -- this is always `'en'` or `'hi'`. */
  language: string;
  state?: string | null;
}

export type AskOutcome =
  | { kind: "answer"; data: AnswerData }
  | { kind: "refused"; status: number; data: RefusalData };

/** Raised only for a malformed 200 response -- the server claimed success
 * but the body does not match `AnswerData`'s shape. Every other failure mode
 * (network error, timeout, a non-2xx response with no usable body) now
 * resolves to a `{kind: 'refused'}` outcome carrying the static fallback
 * helplines instead of throwing -- see `fallbackRefusalData` below. */
export class AskError extends Error {}

function isSourceCard(value: unknown): value is SourceCard {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).chunk_id === "string" &&
    typeof (value as Record<string, unknown>).source_url === "string"
  );
}

function isHelpline(value: unknown): value is Helpline {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).number === "string" &&
    typeof (value as Record<string, unknown>).label === "string"
  );
}

/**
 * Shape-check a parsed body before it is ever cast and handed to a
 * component (Task 6 review, M2). Without this, a response missing
 * `sources` (say) reached `AnswerView.tsx`'s `data.sources.length` as a
 * `TypeError` thrown *during render*, outside this module's `try`/`catch`
 * and with no `error.tsx` boundary in `web/app/` -- i.e. a blank page. A
 * failed check here becomes the ordinary, already-handled `AskError` path
 * instead, matching the project-wide "validate at system boundaries" rule
 * (`AskRequest`'s own field validators do the same on the Python side).
 */
function isAnswerData(value: unknown): value is AnswerData {
  if (typeof value !== "object" || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.answer === "string" &&
    Array.isArray(body.sources) &&
    body.sources.every(isSourceCard) &&
    typeof body.language === "string" &&
    typeof body.corpus_version === "string" &&
    typeof body.footer === "string" &&
    (body.crisis_response === null || typeof body.crisis_response === "string") &&
    Array.isArray(body.helplines) &&
    body.helplines.every(isHelpline) &&
    typeof body.is_answerable === "boolean" &&
    typeof body.quota_remaining === "number"
  );
}

/**
 * A `RefusalData` body carries at minimum `detail`, `escalation_footer` and
 * `helplines` on every refusal path (429 or 500 -- see `types.ts`'s
 * docstring); the quota-specific fields are optional, so they are not
 * checked here.
 */
function isRefusalData(value: unknown): value is RefusalData {
  if (typeof value !== "object" || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.detail === "string" &&
    typeof body.escalation_footer === "string" &&
    (body.crisis_response === null || typeof body.crisis_response === "string") &&
    Array.isArray(body.helplines) &&
    body.helplines.every(isHelpline)
  );
}

/**
 * Build a `RefusalData` from the static client-side fallback
 * (`lib/fallbackHelplines.ts`) for the case the server told us nothing at
 * all -- see that module's docstring for why this is not the prose
 * pattern-matcher C2 removed. Used for a network failure, a timeout, or a
 * non-2xx response whose body is not recognisable `RefusalData` JSON (Task
 * 6 second fix round, re-review HIGH).
 */
function fallbackRefusalData(language: string): RefusalData {
  const fallback = getFallbackHelplineSet(language);
  return {
    detail: getStrings(language).genericError,
    escalation_footer: fallback.escalationFooter,
    crisis_response: fallback.crisisResponse,
    helplines: fallback.helplines,
  };
}

/**
 * Perform the fetch. Returns the `Response` on success; on a hard network
 * failure or a stalled request (H4's `AbortSignal.timeout`), returns `null`
 * rather than throwing -- `askQuestion` turns that into the static fallback
 * refusal, not a bare error, since a connectivity failure is exactly the
 * moment the person asking may need a helpline the most.
 */
async function postQuestion(params: AskParams): Promise<Response | null> {
  try {
    return await fetch(ASK_ENDPOINT, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: params.question,
        language: params.language,
        state: params.state ?? null,
      }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    return null;
  }
}

/**
 * Ask one question and return either a verified answer or a structured
 * refusal. Only throws `AskError` for a malformed 200 body (see the class
 * docstring) -- every other failure resolves to a `{kind: 'refused'}`
 * outcome, never a dead end.
 *
 * **Task 6 review, C1.** A prior version only treated HTTP 429 this way and
 * discarded any other non-2xx body, including the 500 body's
 * `escalation_footer`/`crisis_response` -- the exact dead end
 * `_unhandled_error_content` (`app.py`) was written to prevent. Checking
 * for `escalation_footer` on the body, not the status code, closed that for
 * a well-formed 500. It did not close it for an INFRASTRUCTURE-level
 * failure -- a proxy/CDN/load balancer's own error page, an empty body, or
 * no response at all -- where there is no server JSON to check in the first
 * place.
 *
 * **Task 6 second fix round, re-review HIGH.** That gap is closed here: a
 * network error, a timeout, or a non-2xx body that fails `isRefusalData`
 * (unparseable, empty, or missing `escalation_footer`) all resolve to
 * `fallbackRefusalData` -- the client's own static helpline set -- instead
 * of throwing. The principle is that the client must be able to deliver
 * help with zero server cooperation: an outage is exactly when the server
 * cannot be relied on, and it is also exactly when someone may be in
 * trouble.
 */
export async function askQuestion(params: AskParams): Promise<AskOutcome> {
  const strings = getStrings(params.language);
  const response = await postQuestion(params);

  if (response === null) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(params.language),
    };
  }

  const body: unknown = await response.json().catch(() => null);

  if (response.ok) {
    if (!isAnswerData(body)) {
      throw new AskError(strings.malformedResponseError);
    }
    return { kind: "answer", data: body };
  }

  if (isRefusalData(body)) {
    return { kind: "refused", status: response.status, data: body };
  }

  // The server itself could not tell us anything usable about this failure
  // -- a proxy/CDN/load balancer's own error page, an empty body, or JSON
  // that is not shaped like a refusal. Fall back to the static helpline set
  // rather than a bare error message (see the function docstring's C1 note).
  return {
    kind: "refused",
    status: response.status,
    data: fallbackRefusalData(params.language),
  };
}
