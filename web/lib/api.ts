import { localUploadProblem, uploadRefusal } from "./documents";
import { getFallbackHelplineSet } from "./fallbackHelplines";
import { getStrings } from "./strings";
import type {
  AnswerData,
  ConversationViewData,
  DocumentCitationCard,
  DocumentSummaryData,
  Helpline,
  RefusalData,
  SourceCard,
} from "./types";

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

/**
 * The conversation/document surface (`openlaw.api.artifacts`), proxied the
 * same same-origin way `/ask` is and for the same cookie reason. Exported
 * because `DocumentUpload` renders `DOCUMENTS_ENDPOINT` as a real form
 * `action`: with JavaScript off there is no `fetch` to call, and a file input
 * inside a form that posts nowhere would silently discard what a person
 * chose.
 */
export const DOCUMENTS_ENDPOINT = "/api/openlaw/documents";
export const CONVERSATIONS_ENDPOINT = "/api/openlaw/conversations";

//: A stalled request on a flaky mobile connection must not pin the UI at
//: "Asking…" forever (Task 6 review, H4) -- 30s is generous for a legal-answer
//: round trip (retrieval + generation) while still bounding the wait.
const REQUEST_TIMEOUT_MS = 30_000;

//: Uploads get their own, much longer budget: 10 MiB over a slow mobile
//: uplink can legitimately take minutes, and aborting a transfer that was
//: progressing fine would cost the person their whole document for nothing.
const UPLOAD_TIMEOUT_MS = 180_000;

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
  /** The conversation this turn continues, or `null`/absent to start one. */
  conversationId?: string | null;
  /** Documents already uploaded to `conversationId` whose text this turn may
   * quote. Ids only -- no bytes travel through `/ask`, so the upload path's
   * size, type and page caps cannot be bypassed here. */
  documentIds?: readonly string[];
}

export type AskOutcome =
  | { kind: "answer"; data: AnswerData }
  | { kind: "refused"; status: number; data: RefusalData };

/**
 * Deliberately checks only the two fields a citation is useless without: the
 * id it is keyed by and the URL the reader clicks. `kind` and the judgment
 * fields (Plan 6 Task 7) are required on the wire but not asserted here --
 * a deploy skew that omitted them would otherwise turn every answer into a
 * refusal, and `SourceList`/`classifySource` already degrade an absent value
 * to the neutral "Government document" label rather than a wrong one. A
 * vaguer label beats a blank page.
 */
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
 * failed check here becomes the ordinary, already-handled `{kind: 'refused'}`
 * fallback path instead (final review, H3), matching the project-wide
 * "validate at system boundaries" rule (`AskRequest`'s own field validators
 * do the same on the Python side).
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
    typeof body.quota_remaining === "number" &&
    isOptionalString(body.conversation_id) &&
    (body.turn_index === undefined || typeof body.turn_index === "number") &&
    isOptionalNullableString(body.document_answer) &&
    isOptionalArrayOf(body.document_citations, isDocumentCitationCard)
  );
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || typeof value === "string";
}

function isOptionalNullableString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === "string";
}

function isOptionalArrayOf(
  value: unknown,
  check: (item: unknown) => boolean,
): boolean {
  if (value === undefined) return true;
  return Array.isArray(value) && value.every(check);
}

/**
 * A document citation is checked on all four of its fields, unlike
 * `isSourceCard`, which checks two. The reason for the difference is the
 * consequence of a bad value: a vague source label still links somewhere
 * real, while a citation missing its `filename` or `page_number` tells a
 * reader nothing about WHERE in their own paperwork a claim came from -- and
 * an unverifiable statement about someone's lease is exactly what this
 * section must never present.
 */
function isDocumentCitationCard(value: unknown): value is DocumentCitationCard {
  if (typeof value !== "object" || value === null) return false;
  const card = value as Record<string, unknown>;
  return (
    typeof card.excerpt_id === "string" &&
    typeof card.document_id === "string" &&
    typeof card.filename === "string" &&
    typeof card.page_number === "number"
  );
}

function isDocumentSummary(value: unknown): value is DocumentSummaryData {
  if (typeof value !== "object" || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.id === "string" &&
    typeof body.filename === "string" &&
    typeof body.page_count === "number" &&
    typeof body.conversation_id === "string" &&
    (body.expires_at === null || typeof body.expires_at === "string") &&
    typeof body.retention_notice === "string" &&
    typeof body.quota_remaining === "number"
  );
}

function isConversationView(value: unknown): value is ConversationViewData {
  if (typeof value !== "object" || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.conversation_id === "string" &&
    Array.isArray(body.turns) &&
    body.turns.every(isConversationTurnCard)
  );
}

function isConversationTurnCard(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const turn = value as Record<string, unknown>;
  return (
    typeof turn.turn_index === "number" &&
    typeof turn.question === "string" &&
    typeof turn.answer === "string" &&
    isOptionalNullableString(turn.document_answer) &&
    typeof turn.language === "string"
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
        conversation_id: params.conversationId ?? null,
        document_ids: params.documentIds ?? [],
      }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    return null;
  }
}

/**
 * Ask one question and return either a verified answer or a structured
 * refusal. **Total: never throws.** Every failure mode -- a network error, a
 * timeout, a non-2xx response with no usable body, or (final review, H3) a
 * 200 response whose body does not match `AnswerData`'s shape -- resolves to
 * a `{kind: 'refused'}` outcome, never a dead end.
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
 * of throwing.
 *
 * **Final whole-plan review, H3.** A sixth no-server-cooperation state was
 * missed by that round: HTTP 200 with a body that fails `isAnswerData` --
 * reachable when `web` is rebuilt without `OPENLAW_API_URL` (or the
 * `rewrites()` entry moves) and `/api/openlaw/ask` falls through to Next's
 * own catch-all, which answers 200 with the SPA's own HTML/JSON shell. That
 * used to `throw AskError`, which `page.tsx` rendered as a bare, helpline-
 * free error message on a first question. It now falls back to
 * `fallbackRefusalData` like every other server-told-us-nothing case -- see
 * `lib/__tests__/api.test.ts`'s coverage of this exact case. The principle
 * is that the client must be able to deliver help with zero server
 * cooperation: an outage (or a misconfigured deployment) is exactly when
 * the server cannot be relied on, and it is also exactly when someone may
 * be in trouble.
 */
export async function askQuestion(params: AskParams): Promise<AskOutcome> {
  const response = await postQuestion(params);

  if (response === null) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(params.language),
    };
  }

  const body: unknown = await response.json().catch(() => null);

  if (response.ok && isAnswerData(body)) {
    return { kind: "answer", data: body };
  }

  if (response.ok) {
    // H3: a 200 whose body isn't a real AnswerData is still a case where
    // the server told us nothing usable -- same fallback as any other
    // no-cooperation state, not a thrown error with no helplines.
    return {
      kind: "refused",
      status: response.status,
      data: fallbackRefusalData(params.language),
    };
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

/**
 * Only characters that cannot change what a URL means.
 *
 * The server validates these ids as UUIDs (`schemas.validated_uuid`) and is
 * the authority on them; this is not a second copy of that rule. It is the
 * narrower client-side one: an id is about to be interpolated into a path, so
 * anything containing `/`, `.` or `%` must not be sent at all, whatever it
 * would have meant to the server.
 */
function isSafePathSegment(value: string): boolean {
  return /^[A-Za-z0-9_-]{1,64}$/.test(value);
}

export interface UploadParams {
  file: File | null;
  /** `null` on a first upload -- the server mints the conversation and returns
   * its id, which the page then reuses for every later turn. */
  conversationId: string | null;
  language: string;
  /** How many documents this conversation already holds, so an upload that
   * cannot be accepted costs nothing on a slow connection. */
  heldDocumentCount: number;
}

export type UploadOutcome =
  | { kind: "document"; data: DocumentSummaryData }
  | { kind: "refused"; status: number; data: RefusalData };

/**
 * Upload one document. **Total: never throws**, exactly as `askQuestion` is.
 *
 * Every failure -- a locally detectable one (nothing chosen, wrong type, over
 * the size cap, conversation already full), a server refusal (too large,
 * unsupported type, extraction failed, too many documents, uploads disabled),
 * a network error, a timeout, a 2xx with an unrecognisable body -- resolves to
 * a `{kind: "refused"}` outcome carrying helplines. Requirement 6 does not
 * have an exception for uploads: the person who just failed to attach their
 * eviction notice is still the person who may need a phone number.
 *
 * No `Content-Type` header is set anywhere here. `fetch` derives it from the
 * `FormData` body together with the multipart boundary it generates; setting
 * it by hand omits that boundary and the server cannot parse the body at all.
 */
export async function uploadDocument(params: UploadParams): Promise<UploadOutcome> {
  const problem = localUploadProblem(params.file, params.heldDocumentCount);
  if (problem !== null || params.file === null) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: uploadRefusal(problem ?? "no_file", params.language),
    };
  }

  const form = new FormData();
  form.append("file", params.file);
  form.append("language", params.language);
  if (params.conversationId !== null) {
    form.append("conversation_id", params.conversationId);
  }

  const response = await send(DOCUMENTS_ENDPOINT, {
    method: "POST",
    credentials: "include",
    body: form,
    signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
  });
  if (response === null) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(params.language),
    };
  }

  const body: unknown = await response.json().catch(() => null);
  if (response.ok && isDocumentSummary(body)) {
    return { kind: "document", data: body };
  }
  return { kind: "refused", status: response.status, data: refusalFrom(body, params.language) };
}

/**
 * Perform one request, returning `null` for a hard network failure or a
 * timeout instead of throwing. The shared half of every total function in
 * this module.
 */
async function send(url: string, init: RequestInit): Promise<Response | null> {
  try {
    return await fetch(url, init);
  } catch {
    return null;
  }
}

/** A server refusal body when it is one, the static fallback when it is not. */
function refusalFrom(body: unknown, language: string): RefusalData {
  return isRefusalData(body) ? body : fallbackRefusalData(language);
}

export type DeleteOutcome =
  | { kind: "deleted" }
  | { kind: "refused"; status: number; data: RefusalData };

interface DeleteParams {
  language: string;
}

/**
 * Delete, for a single path-addressed resource. Shared by the two public
 * delete functions below, which differ only in which collection they address.
 */
async function requestDelete(
  collection: string,
  id: string,
  language: string,
): Promise<DeleteOutcome> {
  if (!isSafePathSegment(id)) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(language),
    };
  }
  const response = await send(`${collection}/${id}`, {
    method: "DELETE",
    credentials: "include",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (response === null) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(language),
    };
  }
  if (response.ok) {
    return { kind: "deleted" };
  }
  const body: unknown = await response.json().catch(() => null);
  return { kind: "refused", status: response.status, data: refusalFrom(body, language) };
}

/**
 * Delete one of the caller's own documents. **Total: never throws.**
 *
 * This is the only way a person can withdraw a file they uploaded: an
 * authenticated document is kept until they remove it, so a broken or missing
 * delete affordance would mean the retention promise the upload made could
 * never be exercised. A failure here therefore reports itself loudly (with
 * help attached) rather than quietly dropping the row from the list -- showing
 * a document as gone when the server still holds it would be the worse lie.
 */
export function deleteDocument(
  params: DeleteParams & { documentId: string },
): Promise<DeleteOutcome> {
  return requestDelete(DOCUMENTS_ENDPOINT, params.documentId, params.language);
}

/**
 * Delete a whole conversation -- its turns, its documents, its excerpts.
 * **Total: never throws.** The audit rows deliberately survive it
 * server-side (`openlaw.api.artifacts.remove_conversation`).
 */
export function deleteConversation(
  params: DeleteParams & { conversationId: string },
): Promise<DeleteOutcome> {
  return requestDelete(CONVERSATIONS_ENDPOINT, params.conversationId, params.language);
}

export type ConversationOutcome =
  | { kind: "conversation"; data: ConversationViewData }
  | { kind: "refused"; status: number; data: RefusalData };

/**
 * Re-read a conversation after a reload. **Total: never throws.**
 *
 * A 404 here is ordinary rather than exceptional: an anonymous conversation
 * expires, and the cookie that identified its owner can be cleared by the
 * browser at any time. The page treats a refusal as "start fresh" and forgets
 * the stored id -- never as an error to put in front of the reader, who did
 * nothing wrong.
 */
export async function readConversation(
  params: DeleteParams & { conversationId: string },
): Promise<ConversationOutcome> {
  if (!isSafePathSegment(params.conversationId)) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(params.language),
    };
  }
  const response = await send(`${CONVERSATIONS_ENDPOINT}/${params.conversationId}`, {
    method: "GET",
    credentials: "include",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (response === null) {
    return {
      kind: "refused",
      status: NO_RESPONSE_STATUS,
      data: fallbackRefusalData(params.language),
    };
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.ok && isConversationView(body)) {
    return { kind: "conversation", data: body };
  }
  return {
    kind: "refused",
    status: response.status,
    data: refusalFrom(body, params.language),
  };
}
