import type { AttachedDocument } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

interface DocumentListProps {
  documents: readonly AttachedDocument[]
  /** Every label here renders in this language. */
  language: string
  /** Called with the document's id. The page performs the delete. */
  onDelete: (documentId: string) => void
  /** The document currently being deleted, whose button is disabled so a
   * double tap on a slow connection cannot fire two requests. */
  deletingId: string | null
}

/** A rubbish bin. Paired with a word, never the only signal. */
const BIN_PATH =
  'M7.5 2.5h5l.7 1.3H17v1.7H3V3.8h3.8l.7-1.3ZM4.6 7h10.8l-.8 9.6a1.5 1.5 0 0 1-1.5 1.4H6.9a1.5 1.5 0 0 1-1.5-1.4L4.6 7Zm3 1.8v6.5h1.6V8.8H7.6Zm3.2 0v6.5h1.6V8.8h-1.6Z'

/**
 * The documents attached to this conversation, each with a way to remove it.
 *
 * **The delete button is the point of this component.** An authenticated
 * upload is retained until the person removes it, so this interface is the
 * only means by which they can exercise that -- there is no other screen, no
 * support address, no automatic expiry to fall back on. It is therefore a
 * full-size, always-visible control (never behind a menu or a swipe), at
 * least 48px tall and wide for a thumb, with the filename in its accessible
 * name so a screen-reader user is never asked to delete "button" and hope.
 *
 * `retentionNotice` is the server's own sentence, rendered verbatim: it is
 * the promise made at upload time, and paraphrasing it here could promise
 * something the server does not do.
 *
 * Styled as the reader's paperwork (warm ground, dashed edges, paperclip
 * family) rather than as a legal surface -- the same visual language
 * `DocumentCitationList` uses, so "my document" looks the same everywhere it
 * appears on the page.
 */
export default function DocumentList({
  documents,
  language,
  onDelete,
  deletingId,
}: DocumentListProps) {
  if (documents.length === 0) {
    return null
  }
  const strings = getStrings(language)
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex flex-col gap-2">
        {documents.map((document) => (
          <li
            key={document.id}
            className="flex items-start justify-between gap-3 rounded-xl border-2 border-dashed border-paper-edge bg-paper p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="font-semibold break-all text-paper-ink">{document.filename}</p>
              <p className="text-sm tabular-nums text-muted">
                {strings.documentPageCount(document.pageCount)}
              </p>
              <p className="mt-1 text-sm leading-relaxed text-muted">
                {document.retentionNotice}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onDelete(document.id)}
              disabled={deletingId === document.id}
              aria-label={strings.deleteDocumentLabel(document.filename)}
              // Red is reserved entirely for the crisis block (`app/globals.css`)
              // -- deleting your own file is not an emergency, so this is a
              // plain bordered control, sized for a thumb rather than coloured
              // for alarm.
              className="flex min-h-12 min-w-12 shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg border-2 border-paper-edge bg-white px-3 text-xs font-semibold text-paper-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="h-5 w-5"
                fill="currentColor"
              >
                <path d={BIN_PATH} />
              </svg>
              {strings.deleteDocumentButton}
            </button>
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted">{strings.attachedToNextTurnLabel}</p>
    </div>
  )
}
