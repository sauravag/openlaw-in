"""Constants for user-uploaded document artifacts (Plan 6).

This module is the single place every fixed limit and content-shape constant
for the conversations-and-artifacts feature is defined. No later task in
Plan 6 may re-derive or hard-code one of these values at a call site -- they
import it from here, so a limit can be audited and changed in one place.

Scope, per the plan's spec amendment: uploaded documents may be read and
quoted from (PDF, DOCX, plain text); there is no OCR (so an image-only PDF
is refused, not silently returned empty -- see `ALLOWED_CONTENT_TYPES`
below, which excludes image types entirely), no document drafting, and no
case tracking.

`Settings.uploads_enabled` (see `openlaw.config`) is the runtime kill
switch; the constants below bound cost and context size once uploads are on.
"""

# Content types this plan can extract text from. Deliberately excludes
# `image/jpeg` and `image/png` (and every other image type): there is no OCR
# in this plan (see Task 4 of the conversations-and-artifacts plan) -- an
# image-only upload must be refused with a clear "unsupported type" error,
# never silently accepted and yield zero extracted text.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
# Prefix marking an excerpt id as coming from a user-uploaded document
# rather than the law corpus. A later task's citation validator drops any
# `Claim` whose id starts with this prefix (contamination: a document quote
# must never be presented as if it were a citable legal source) and,
# symmetrically, drops any `DocumentClaim` whose id does NOT start with it.
DOCUMENT_EXCERPT_PREFIX = "doc:"
# Maximum characters in a single extracted excerpt.
MAX_EXCERPT_CHARS = 1_200
# Maximum number of excerpts carried into a single answer's context.
MAX_DOCUMENT_EXCERPTS = 6
# Maximum total characters of document context assembled into one prompt,
# across all excerpts.
MAX_DOCUMENT_CONTEXT_CHARS = 12_000
# Maximum number of prior conversation turns replayed into a new answer.
MAX_HISTORY_TURNS = 6
# Maximum number of previously-cited corpus chunks carried forward across
# turns in a conversation.
MAX_CARRIED_CHUNKS = 8
# Maximum total characters of conversation history rendered into one
# prompt, across all replayed turns. A turn count alone is not a bound --
# the same lesson `openlaw.answers.generate.MAX_CONTEXT_CHARS` records for
# retrieved chunks: six turns of unbounded claim text is unbounded input.
# 8,000 characters is roughly 2-3k tokens and leaves the ordinary case (a
# handful of one- or two-sentence claims per turn) entirely untouched.
# Counted in characters, not tokens, exactly as MAX_CONTEXT_CHARS is: an
# Indic-script history costs more tokens per character than an English one,
# so the token figure is the pessimistic one, but the two languages get the
# identical character allowance and neither is truncated sooner.
MAX_HISTORY_CHARS = 8_000
# Pages per quota unit when costing an upload: an upload costs
# max(1, ceil(page_count / PAGES_PER_QUOTA_UNIT)) quota units.
PAGES_PER_QUOTA_UNIT = 5
# Characters of plain text treated as one synthetic page (Plan 6 Task 15).
# `text/plain` had no page notion at all: `_extract_text_pages` returned a
# single page regardless of length, so `Settings.max_document_pages` could
# never fire for it and a 10 MiB text upload cost ONE quota unit (page_count
# 1) while producing roughly 8,700 excerpts -- a hole both in metering and in
# stored volume that PDF uploads never had. Paginating plain text closes it
# with the caps that already exist rather than a second, text-only limit:
# 10 MiB becomes ~3,500 synthetic pages, which `max_document_pages` refuses
# as `too_many_pages`, and an accepted text document is bounded at
# `max_document_pages * CHARS_PER_TEXT_PAGE` characters. 3,000 characters is
# about one densely typed A4 page, so an ordinary one-page notice stays one
# page and the existing fixtures are unaffected.
CHARS_PER_TEXT_PAGE = 3_000
