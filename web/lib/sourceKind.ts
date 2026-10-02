import type { SourceCard } from './types'

/**
 * What kind of legal material a citation points at.
 *
 * **Read off the response, not guessed.** The backend knows this exactly --
 * `openlaw.retrieval.context.ChunkKind` is set from the Qdrant payload
 * (`chunk_from_point`: a `case_id` means a judgment) -- and since Plan 6
 * Task 7 it is on the wire as `SourceCard.kind`. This file used to infer it
 * instead, from the URL's hostname plus whether `title` was null, because
 * `openlaw.answers.validate.Source` dropped the real value on the way out.
 * That heuristic was conservative but still capable of putting the wrong
 * kind of authority on a legal citation; it is gone.
 *
 * `document` stays as the honest "we cannot tell" label for a value this
 * build does not recognise -- a server that grows a third `ChunkKind` gets
 * a neutral word here, never a wrong one.
 *
 * **`'document'` here has nothing to do with an uploaded document, and the
 * collision of names is a real trap.** This value classifies a CORPUS chunk
 * whose kind this build cannot label -- a government source that still has a
 * `source_url` and still renders as a link. A file the reader uploaded is a
 * different thing entirely: it has no URL, never reaches `SourceCard` or
 * `SourceList`, and renders through `components/DocumentCitationList.tsx`
 * from `DocumentCitationCard`, which has no `source_url` field to give it
 * one. Nothing in the upload path may reuse this value, and nothing here may
 * grow an upload case. Renaming either would be churn; knowing which is
 * which is the requirement.
 *
 * No court is ever inferred from a hostname, and none is composed from
 * `court_code` either: a bucket name is not a jurisdiction and `"3_22"` is
 * not the name of a court, so printing one would be fabricating provenance.
 */
export type SourceKind = 'statute' | 'judgment' | 'document'

/** `ChunkKind` (the wire value) to the word this interface shows. */
const KIND_BY_CHUNK_KIND: Record<string, SourceKind> = {
  provision: 'statute',
  judgment: 'judgment',
}

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

/** Classify one citation from the kind the server sent. */
export function classifySource(source: SourceCard): SourceKind {
  return KIND_BY_CHUNK_KIND[source.kind] ?? 'document'
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
