import type { SourceCard } from './types'

/**
 * What kind of legal material a citation points at, derived client-side.
 *
 * **Why this is derived rather than read off the response.** The backend
 * knows the answer exactly -- `openlaw.retrieval.context.ChunkKind` is set
 * from the Qdrant payload (`chunk_from_point`: a `case_id` means a
 * judgment) -- but that field is dropped twice on the way out:
 * `openlaw.answers.validate.Source` keeps only `chunk_id`/`source_url`/
 * `title`, and `openlaw.api.schemas.SourceCard` mirrors exactly those three.
 * So `/ask` gives the browser no `kind`, no `case_id`, no `decision_date`,
 * no `judges` and no `headnote` -- see the redesign report. The right fix is
 * to widen `SourceCard`; until then this classifies from what is actually on
 * the wire.
 *
 * It is deliberately conservative, because a wrong label on a legal citation
 * is worse than a vague one:
 *
 * - `statute` is claimed only for an India Code host. That is where every
 *   `acts_india_v1` provision's `source_url` points
 *   (`openlaw.corpus.sources.resolve_provision_url`), and nothing else in
 *   the corpus is published there.
 * - `judgment` is claimed only for a citation with NO title. Judgment
 *   payloads carry no `title` key at all (`openlaw.index.upsert.build_point`
 *   / `hc_upsert.build_hc_point`), so `title === null` plus a non-statute
 *   host is exactly the judgment case today.
 * - `document` is the honest "we cannot tell from here" label -- it never
 *   asserts anything the payload does not support.
 *
 * No court is ever inferred from a hostname: a bucket name is not a
 * jurisdiction, and naming the wrong court would be fabricating provenance.
 */
export type SourceKind = 'statute' | 'judgment' | 'document'

/** India Code -- the government's statute repository. */
const STATUTE_HOST = 'indiacode.nic.in'

/**
 * The bare hostname of `url`, or `null` when it is not a URL.
 *
 * Shown under a citation so the reader can see where a link will take them
 * without the page having to print a 120-character tarball path at them.
 */
export function sourceHost(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return null
  }
}

/** Classify one citation. See `SourceKind` for why this is so cautious. */
export function classifySource(source: SourceCard): SourceKind {
  const host = sourceHost(source.source_url)
  if (host === null) {
    return 'document'
  }
  if (host === STATUTE_HOST || host.endsWith(`.${STATUTE_HOST}`)) {
    return 'statute'
  }
  return source.title === null ? 'judgment' : 'document'
}

/**
 * Only an `https:` source is ever rendered as a clickable link (Task 6
 * review, M6) -- defence in depth, since `source_url` is corpus-controlled
 * today, not caller-supplied. A non-`https:` value still renders as plain
 * text rather than being silently dropped.
 */
export function isSafeSourceUrl(url: string): boolean {
  try {
    return new URL(url).protocol === 'https:'
  } catch {
    return false
  }
}
