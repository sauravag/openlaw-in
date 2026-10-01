import { getStrings } from '@/lib/strings'
import type { DocumentCitationCard } from '@/lib/types'

interface DocumentCitationListProps {
  /**
   * What the reader's own document says, as the server composed it -- its own
   * "this is what your paper says, not the law" lead-in included. Rendered
   * verbatim and never reassembled here: the distinction is baked into the
   * string on the server precisely so a client cannot lose it.
   */
  documentAnswer: string | null
  citations: readonly DocumentCitationCard[]
  /** Every label here renders in this language. */
  language: string
}

/** A paperclip. The one glyph on this page that means "your paperwork". */
const PAPERCLIP_PATH =
  'M12.9 3.6a3.3 3.3 0 0 1 4.7 4.7l-7.1 7.1a1.7 1.7 0 0 1-2.4-2.4l6.4-6.4 1.2 1.2-6.4 6.4 1.2-1.2-1.2 1.2 7.1-7.1a1.6 1.6 0 0 0-2.3-2.3l-7.8 7.8a3.5 3.5 0 0 0 5 5l6.4-6.4 1.2 1.2-6.4 6.4a5.2 5.2 0 0 1-7.4-7.4l7.8-7.8Z'

/**
 * Everything in an answer that came from the reader's OWN document.
 *
 * **This is the most important visual boundary on the page**, and it is drawn
 * four independent ways so that losing any one of them still leaves the other
 * three:
 *
 * 1. **Words.** `strings.documentNotLawNote` -- "This is your own document,
 *    not law." -- sits at the top of the section, in full size, before any
 *    quoted content. The server's own lead-in sentence says it again inside
 *    the prose. That redundancy is deliberate; this is the one place on the
 *    page where over-stating is the safer failure.
 * 2. **Shape.** A dashed edge and a paperclip, where every legal surface on
 *    the page is solid-edged and carries a book, scales or page glyph.
 * 3. **Colour.** The warm `paper` ground against the cool whites and
 *    blue-greys the law gets (`app/globals.css`).
 * 4. **The absence of links.** Not one anchor is rendered in this section,
 *    and there is nothing to render one from: `DocumentCitationCard` has no
 *    `source_url` field (`lib/types.ts`). A reader who has learned that a
 *    blue underlined label means "a government source you can open" finds
 *    none of them here.
 *
 * Each citation is a filename and a page number, because that is the only
 * verification available for a document only the reader holds: nobody else
 * can check it, so the interface's job is to say exactly where in their own
 * paperwork to look.
 *
 * Renders nothing at all when a turn produced no document content -- an empty
 * "From your document" heading would imply a document was read when none was.
 */
export default function DocumentCitationList({
  documentAnswer,
  citations,
  language,
}: DocumentCitationListProps) {
  if (!documentAnswer && citations.length === 0) {
    return null
  }
  const strings = getStrings(language)
  return (
    <section
      aria-label={strings.documentCitationsHeading}
      data-testid="document-findings"
      className="flex flex-col gap-3 rounded-xl border-2 border-dashed border-paper-edge bg-paper p-4"
    >
      <h2 className="flex items-center gap-2 text-sm font-bold text-paper-ink">
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-4 w-4 shrink-0"
          fill="currentColor"
        >
          <path d={PAPERCLIP_PATH} />
        </svg>
        {strings.documentCitationsHeading}
      </h2>

      <p className="text-base font-semibold text-paper-ink">{strings.documentNotLawNote}</p>

      {documentAnswer && (
        <p
          data-testid="document-answer"
          className="border-l-4 border-paper-edge pl-3 text-lg leading-relaxed whitespace-pre-line text-ink"
        >
          {documentAnswer}
        </p>
      )}

      {citations.length > 0 && (
        <ul className="flex flex-col gap-2">
          {citations.map((citation) => (
            <li
              key={citation.excerpt_id}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border border-dashed border-paper-edge px-3 py-2"
            >
              {/* Plain text, never a link, never a label that could be read
                * as a citation of law. `filename` was sanitised at the upload
                * boundary (`openlaw.documents.fence.sanitise`). */}
              <span className="font-semibold break-all text-paper-ink">
                {citation.filename}
              </span>
              <span className="text-sm tabular-nums text-muted">
                {strings.documentPageLabel(citation.page_number)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
