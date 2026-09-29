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
 * One citation card.
 *
 * **The display label never invents anything.** `title` is used when the API
 * sends one; when it does not -- which is every judgment today, because
 * `openlaw.api.schemas.SourceCard` carries no `case_id`, `decision_date`,
 * `judges` or `headnote` to build a citation from -- the label falls back to
 * the kind ("Court judgment"), not to `null`, and not to the raw tarball URL
 * the previous UI printed. Composing "Case 1963_INSC_244" here would be
 * fabricating a citation the response does not support, and naming a court
 * from a hostname would be fabricating provenance. See `lib/sourceKind.ts`.
 *
 * The URL still has to be reachable -- that is the product's whole trust
 * premise -- so the host is printed underneath in plain words, outside the
 * link, where it informs the reader without swallowing the link's
 * accessible name.
 */
function SourceItem({ source, strings }: { source: SourceCard; strings: UIStrings }) {
  const kind = classifySource(source)
  const label = source.title ?? kindLabel(kind, strings)
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
        {source.title !== null && (
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
