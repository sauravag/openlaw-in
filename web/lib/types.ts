/**
 * Shapes mirroring `openlaw.api.schemas` (`src/openlaw/api/schemas.py`).
 *
 * Kept as plain, hand-written types rather than generated from the FastAPI
 * OpenAPI schema: this task has no build step wired to the Python service,
 * and these three shapes are small and stable enough that duplication is
 * cheaper than a codegen pipeline (YAGNI). If the API response shape drifts,
 * `AnswerView.test.tsx` and `RefusalNotice.test.tsx` are the tripwire.
 */

/**
 * What kind of legal material a citation points at. Mirrors
 * `openlaw.retrieval.context.ChunkKind`, serialised by value.
 */
export type ChunkKind = 'provision' | 'judgment'

/**
 * One verified citation of the LAW, safe to render because every retrievable
 * chunk carries a `source_url` by construction (Task 4/5). Mirrors
 * `openlaw.api.schemas.SourceCard`.
 *
 * `kind` is sent by the server, which has always known it. Until Plan 6 Task
 * 7 it was not on the wire, and `lib/sourceKind.ts` inferred it from the
 * URL's hostname -- a heuristic that could mislabel the kind of authority a
 * citation carries. The judgment fields below arrived in the same change;
 * each is `null`/empty when the corpus does not record it (no judgment
 * payload has a `title`, no provision payload has a `case_id`), so a
 * renderer must fall back honestly rather than print a placeholder.
 *
 * `judges` and `headnote` arrive but are not rendered (security review
 * LOW-2). That is deliberate, not unfinished: a corpus headnote is English
 * prose, and printing it unconditionally would put English text inside a
 * Hindi answer. Rendering either belongs to the task that designs the
 * citation display, along with how it serves a non-English reader.
 *
 * There is deliberately no shape here for a citation of the user's own
 * uploaded document. `openlaw.answers.validate.DocumentCitation` has no
 * `source_url` and no counterpart on this API: the list a reader treats as
 * legal provenance can only be filled from the corpus.
 */
export interface SourceCard {
  chunk_id: string
  source_url: string
  kind: ChunkKind
  title: string | null
  case_id: string | null
  court_code: string | null
  decision_date: string | null
  judges: string[]
  headnote: string | null
}

/**
 * One pointer into the reader's OWN uploaded document. Mirrors
 * `openlaw.api.schemas.DocumentCitationCard`.
 *
 * **There is no `source_url` here. Not optional, not `null` -- absent.** That
 * is the whole point of the type, and it is load-bearing in TypeScript
 * exactly as it is in pydantic: `SourceList` takes `SourceCard[]`, every
 * element of which must carry a `source_url`, so a document citation cannot
 * be passed to the component that renders government links. The type system
 * refuses it; no reviewer has to notice.
 *
 * `filename` and `page_number` are how a person finds the passage in their
 * own paperwork -- the only verification available for a statement about a
 * document only they hold. `filename` was sanitised at the upload boundary
 * (`openlaw.documents.fence.sanitise`), so it is already safe to render.
 */
export interface DocumentCitationCard {
  excerpt_id: string
  document_id: string
  filename: string
  page_number: number
}

/**
 * One phone number safe to render as a tap-to-call `tel:` link, with a
 * short label naming what it is for. Mirrors `openlaw.answers.escalation
 * .Helpline`.
 *
 * Task 6 fix round, C2. A prior version had the frontend pattern-match a
 * fixed allowlist of numbers against `crisis_response`/`footer` prose to
 * decide what to linkify (`PhoneLinkedText`, `lib/helplineNumbers.ts` --
 * both removed). That could never display a WRONG number, but it depended
 * on every number staying in ASCII digits forever, including under future
 * localisation -- a Hindi crisis message rendered with Devanagari numerals
 * would silently defeat an ASCII-digit regex. `helplines` replaces that: the
 * backend says explicitly which numbers are helplines and what to call
 * them, so `HelplineList` renders `tel:` links straight from this array,
 * never by scanning text for digits.
 */
export interface Helpline {
  number: string
  label: string
}

/**
 * Mirrors `AskResponse`. Deliberately has no `unverified_summary` field --
 * the API never returns one (see `schemas.py`'s module docstring: a model
 * could otherwise hide a hallucination in prose while its claims stayed
 * well-cited), and this type must not invent a place to smuggle one back in.
 */
export interface AnswerData {
  answer: string
  sources: SourceCard[]
  language: string
  corpus_version: string
  footer: string
  crisis_response: string | null
  helplines: Helpline[]
  is_answerable: boolean
  quota_remaining: number
  /**
   * The conversation this turn belongs to, so the next question can continue
   * the thread. Optional here although `AskResponse` requires it, for the
   * reason `lib/api.ts`'s `isSourceCard` gives about `kind`: a deployment
   * where `web` is newer than the API must degrade to "cannot continue the
   * thread", never to "every answer becomes a refusal".
   */
  conversation_id?: string
  /** This turn's 0-based position in the conversation. */
  turn_index?: number
  /**
   * What the reader's OWN uploaded document says -- rendered in its own
   * region (`DocumentCitationList`), never merged into `answer`. Absent or
   * `null` when no document contributed to this turn. The server composes
   * this string with its own "this is what your paper says, not the law"
   * lead-in baked in (`schemas.render_document_claims`), which is why it is
   * rendered verbatim rather than reassembled here.
   */
  document_answer?: string | null
  /** The excerpts `document_answer` points at -- filename and page, no URL. */
  document_citations?: DocumentCitationCard[]
}

/**
 * A refusal, with everything needed to route the user to help instead of a
 * dead end.
 *
 * `detail` and `escalation_footer` are the only fields every refusal body
 * is guaranteed to carry. `QuotaExceeded` (`schemas.py`, HTTP 429 -- an
 * exhausted allowance or a tripped short-burst rate limit) sends all seven
 * fields. The app-level 500 handler's body (`app.py`'s
 * `_unhandled_error_content`) is deliberately narrower -- `detail`,
 * `escalation_footer`, `crisis_response` only, no quota fields at all, since
 * an unhandled error has no quota decision to report -- so the four
 * quota-specific fields are optional here rather than required (Task 6 fix
 * round, C1). `RefusalNotice` renders each one only when present.
 */
export interface RefusalData {
  detail: string
  escalation_footer: string
  crisis_response: string | null
  helplines: Helpline[]
  quota_remaining?: number
  signup_required?: boolean
  quota_reset_at?: string | null
  signup_url?: string | null
}

/**
 * What a successful upload returns. Mirrors
 * `openlaw.api.schemas.DocumentSummary`.
 *
 * `retention_notice` is server-composed prose, already localised, and is
 * rendered verbatim: it is the sentence that tells a person what happens to
 * the file they just handed over, and a client that paraphrased it could
 * promise something the server does not do. `expires_at` is `null` for an
 * authenticated upload (kept until deleted) and a timestamp for an anonymous
 * one.
 */
export interface DocumentSummaryData {
  id: string
  filename: string
  page_count: number
  conversation_id: string
  expires_at: string | null
  retention_notice: string
  quota_remaining: number
}

/**
 * One past turn, replayed after a reload. Mirrors
 * `openlaw.api.schemas.ConversationTurnCard`.
 *
 * Deliberately carries no `sources`: the transcript stores claims, not the
 * citation cards, so a replayed turn has no government links to render and
 * must say so rather than present law with nothing behind it (see
 * `strings.restoredTurnNote`). `answer` and `document_answer` are split here
 * for the same reason they are split on `AnswerData`.
 */
export interface ConversationTurnCardData {
  turn_index: number
  question: string
  answer: string
  document_answer: string | null
  language: string
}

/** A conversation's turns. Mirrors `openlaw.api.schemas.ConversationView`. */
export interface ConversationViewData {
  conversation_id: string
  turns: ConversationTurnCardData[]
}
