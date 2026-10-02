import { getStrings, type UIStrings } from '@/lib/strings'
import { classifySource, isSafeSourceUrl, sourceHost, type SourceKind } from '@/lib/sourceKind'
import type { SourceCard } from '@/lib/types'

interface SourceListProps {
  sources: SourceCard[]
  /** Kind labels and the "Opens <host>" line render in this language. */
  language: string
}

/** Per-kind chip colour. Never the only signal -- see `KIND_ICONS`. */
const KIND_CLASSES: Record<SourceKind, string> = {
  statute: 'bg-statute-soft text-statute',
  judgment: 'bg-judgment-soft text-judgment',
  document: 'bg-docmark-soft text-docmark',
}

/** Per-kind text colour, for the small word above the citation. */
const KIND_TEXT_CLASSES: Record<SourceKind, string> = {
  statute: 'text-statute',
  judgment: 'text-judgment',
  document: 'text-docmark',
}

/**
 * A distinct glyph per kind, so statute and judgment stay distinguishable
 * for a reader with a colour-vision deficiency, in high-contrast mode, or on
 * a cheap screen that washes out the tints. The kind is also spelled out in
 * words next to it.
 */
const KIND_ICONS: Record<SourceKind, string> = {
  // An open book: the statute book.
  statute:
    'M2 4.2c2.4-.9 4.8-.9 7.2 0v11.5c-2.4-.9-4.8-.9-7.2 0V4.2Zm8.8 0c2.4-.9 4.8-.9 7.2 0v11.5c-2.4-.9-4.8-.9-7.2 0V4.2Z',
  // A pair of scales: the court.
  judgment:
    'M9.1 2.2a.9.9 0 0 1 1.8 0v.9h4.6a.9.9 0 0 1 0 1.8h-.5l2.6 5.4c.1 1.7-1.3 3-3.1 3s-3.2-1.3-3.1-3l2.6-5.4h-3.1v11.3h3a.9.9 0 0 1 0 1.8H6.1a.9.9 0 0 1 0-1.8h3V4.9H6l2.6 5.4c.1 1.7-1.3 3-3.1 3s-3.2-1.3-3.1-3l2.6-5.4h-.5a.9.9 0 1 1 0-1.8h4.6v-.9Z',
  // A plain page: everything we cannot classify with certainty.
  document: 'M4.5 2h7L16 6.5V18H4.5V2Zm6.5 1.6v3.2h3.2L11 3.6ZM6.8 10h6.4v1.6H6.8V10Zm0 3.4h6.4V15H6.8v-1.6Z',
}

function kindLabel(kind: SourceKind, strings: UIStrings): string {
  if (kind === 'statute') return strings.sourceKindStatute
  if (kind === 'judgment') return strings.sourceKindJudgment
  return strings.sourceKindDocument
}

/**
 * How a citation names itself, in descending order of what the response
 * actually supports.
 *
 * `title` when the corpus recorded one (every provision). Otherwise the case
 * id, which every judgment payload carries and which -- since Plan 6 Task 7 --
 * actually reaches the browser: printing it is quoting the corpus, not
 * composing a citation. Otherwise the kind ("Court judgment"), never `null`
 * and never the raw tarball URL the first UI printed.
 *
 * Nothing here is assembled from parts: no court is named from `court_code`
 * (`"3_22"` is not a court's name) and no case name is built from judges or
 * dates. A citation the reader cannot check against its own source is worse
 * than a vague one.
 */
function sourceLabel(source: SourceCard, kind: SourceKind, strings: UIStrings): string {
  // Truthiness, not `!== null`: the field is also absent (rather than null)
  // if a server ever omits it, and an empty string is not a name either.
  // Either would otherwise render as a blank link with no accessible name.
  if (source.title) return source.title
  if (source.case_id) return strings.sourceCaseLabel(source.case_id)
  return kindLabel(kind, strings)
}

/**
 * One citation card.
 *
 * **The display label never invents anything** -- see `sourceLabel`. The kind
 * is printed in words above the link whenever the label is something other
 * than the kind itself, so the reader is never shown a bare id with no idea
 * what sort of authority it is.
 *
 * The URL still has to be reachable -- that is the product's whole trust
 * premise -- so the host is printed underneath in plain words, outside the
 * link, where it informs the reader without swallowing the link's
 * accessible name.
 */
function SourceItem({ source, strings }: { source: SourceCard; strings: UIStrings }) {
  const kind = classifySource(source)
  const label = sourceLabel(source, kind, strings)
  const host = sourceHost(source.source_url)
  const isLinkable = isSafeSourceUrl(source.source_url)

  return (
    <div className="flex items-start gap-3 rounded-xl border border-line bg-white p-4">
      <span className={`shrink-0 rounded-lg p-2 ${KIND_CLASSES[kind]}`}>
        <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="currentColor">
          <path d={KIND_ICONS[kind]} />
        </svg>
      </span>
      <div className="min-w-0 flex-1">
        {label !== kindLabel(kind, strings) && (
          <p className={`text-xs font-bold ${KIND_TEXT_CLASSES[kind]}`}>
            {kindLabel(kind, strings)}
          </p>
        )}
        {isLinkable ? (
          <a
            href={source.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-brand underline decoration-2 underline-offset-4 hover:text-brand-ink"
          >
            {label}
          </a>
        ) : (
          <span className="font-semibold text-ink">{label}</span>
        )}
        {source.decision_date ? (
          <p className="mt-1 text-sm text-muted">
            {strings.sourceDecidedOn(source.decision_date)}
          </p>
        ) : null}
        {host !== null && (
          <p className="mt-1 text-sm break-words text-muted">{strings.sourceOpensAt(host)}</p>
        )}
      </div>
    </div>
  )
}

/**
 * The "Where this comes from" section.
 *
 * Every source stays a visible, tappable link to its government URL -- the
 * one thing this product actually promises. Rendered as cards rather than a
 * bare list so each citation is its own tap target and the kind marker has
 * somewhere to live.
 */
export default function SourceList({ sources, language }: SourceListProps) {
  if (sources.length === 0) {
    return null
  }
  const strings = getStrings(language)
  return (
    <section aria-label={strings.sourcesHeading} className="flex flex-col gap-3">
      <h2 className="text-sm font-bold text-muted">
        {strings.sourcesHeading}
      </h2>
      <ul className="flex flex-col gap-3">
        {sources.map((source) => (
          <li key={source.chunk_id}>
            <SourceItem source={source} strings={strings} />
          </li>
        ))}
      </ul>
    </section>
  )
}
