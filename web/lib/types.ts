/**
 * Shapes mirroring `openlaw.api.schemas` (`src/openlaw/api/schemas.py`).
 *
 * Kept as plain, hand-written types rather than generated from the FastAPI
 * OpenAPI schema: this task has no build step wired to the Python service,
 * and these three shapes are small and stable enough that duplication is
 * cheaper than a codegen pipeline (YAGNI). If the API response shape drifts,
 * `AnswerView.test.tsx` and `RefusalNotice.test.tsx` are the tripwire.
 */

/** One verified citation, safe to render because every retrievable chunk
 * carries a `source_url` by construction (Task 4/5). */
export interface SourceCard {
  chunk_id: string
  source_url: string
  title: string | null
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
  is_answerable: boolean
  quota_remaining: number
}

/** Mirrors `QuotaExceeded` -- the body returned on both HTTP 429 causes
 * (exhausted allowance, tripped short-burst rate limit). */
export interface RefusalData {
  detail: string
  quota_remaining: number
  signup_required: boolean
  escalation_footer: string
  crisis_response: string | null
  quota_reset_at: string | null
  signup_url: string | null
}
