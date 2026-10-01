import { DOCUMENTS_ENDPOINT } from '@/lib/api'
import {
  ALLOWED_UPLOAD_EXTENSIONS,
  ANONYMOUS_UPLOAD_TTL_HOURS,
  MAX_DOCUMENTS_PER_CONVERSATION,
  MAX_UPLOAD_MEGABYTES,
  SIGN_IN_PATH,
} from '@/lib/documents'
import { getStrings } from '@/lib/strings'

interface DocumentUploadProps {
  /** Every label here renders in this language. */
  language: string
  /** Sent as a form field so the no-JavaScript post lands in the right
   * conversation; `null` on a first upload, which lets the server mint one. */
  conversationId: string | null
  /** Drives the anonymous-retention notice. Anonymous today -- this project
   * has no browser-side session yet -- but the prop exists so the notice
   * cannot be left behind when it does. */
  isSignedIn: boolean
  isUploading: boolean
  uploadingFilename: string | null
  documentCount: number
  /** Called the moment a file is chosen. The page performs the upload. */
  onFileChosen: (file: File) => void
}

/**
 * The attach-a-document control.
 *
 * **It is a real `<form method="post" encType="multipart/form-data">`,
 * pointed at the real endpoint.** With JavaScript off -- Opera Mini's
 * extreme-saving mode, UC Browser's proxy mode, a bundle that never arrives on
 * 2G -- `onChange` never fires, and a file input inside a form that posts
 * nowhere silently discards whatever a frightened person just spent five
 * minutes finding. Posting to `DOCUMENTS_ENDPOINT` means the file is actually
 * stored; the reader sees the API's plain response rather than this page, which
 * is a poor experience and a far better outcome than losing the document.
 *
 * With JavaScript on, choosing a file uploads it immediately (`onChange`) and
 * the submit is cancelled: a second "now press this" step is a step a person on
 * a cheap phone will not find, and the button exists for the no-JS path.
 *
 * **The limits are stated before a file is chosen.** A refusal a person could
 * have predicted is not a failure, and on a metered connection discovering the
 * 10 MB cap after uploading 10 MB is its own small cruelty.
 *
 * The anonymous notice is not buried and not softened: the document WILL be
 * deleted, the window is named, and the one action that changes it is offered
 * in the same breath. Mirrors what the server will put in
 * `DocumentSummary.retention_notice` after the upload, so the promise is the
 * same before and after.
 */
export default function DocumentUpload({
  language,
  conversationId,
  isSignedIn,
  isUploading,
  uploadingFilename,
  documentCount,
  onFileChosen,
}: DocumentUploadProps) {
  const strings = getStrings(language)
  const isFull = documentCount >= MAX_DOCUMENTS_PER_CONVERSATION

  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const [file] = event.target.files ?? []
    if (file) onFileChosen(file)
    // Clear the input so choosing the same filename twice still fires a
    // change event -- a person whose first attempt failed will try again.
    event.target.value = ''
  }

  return (
    <form
      action={DOCUMENTS_ENDPOINT}
      method="post"
      encType="multipart/form-data"
      onSubmit={(event) => event.preventDefault()}
      className="flex flex-col gap-3"
    >
      <input type="hidden" name="language" value={language} />
      {conversationId !== null && (
        <input type="hidden" name="conversation_id" value={conversationId} />
      )}

      <p className="text-sm leading-relaxed text-muted">{strings.documentsIntro}</p>

      {isFull ? (
        <p className="text-sm font-semibold text-paper-ink">
          {strings.uploadFullNote(MAX_DOCUMENTS_PER_CONVERSATION)}
        </p>
      ) : (
        <>
          <label className="flex flex-col gap-2">
            <span className="text-base font-semibold text-ink">{strings.attachFileLabel}</span>
            <input
              type="file"
              name="file"
              accept={ALLOWED_UPLOAD_EXTENSIONS}
              disabled={isUploading}
              onChange={handleChange}
              className="min-h-12 w-full rounded-xl border-2 border-dashed border-paper-edge bg-paper px-3 py-2.5 text-base text-ink disabled:opacity-50"
            />
          </label>
          <p className="text-sm leading-relaxed text-muted">
            {strings.uploadLimitsNote(MAX_UPLOAD_MEGABYTES, MAX_DOCUMENTS_PER_CONVERSATION)}
          </p>
          {!isSignedIn && (
            <p className="text-sm leading-relaxed text-paper-ink">
              {strings.anonymousUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)}{' '}
              <a
                href={SIGN_IN_PATH}
                className="font-semibold text-brand underline decoration-2 underline-offset-4"
              >
                {strings.signInCta}
              </a>
            </p>
          )}
          {/* Needed only for the no-JavaScript path -- with scripts running,
            * `onChange` has already started the upload before this is reachable. */}
          <button
            type="submit"
            className="min-h-12 self-start rounded-xl border-2 border-brand px-5 text-base font-semibold text-brand"
          >
            {strings.attachButton}
          </button>
        </>
      )}

      {isUploading && uploadingFilename !== null && (
        <p
          role="status"
          className="rounded-xl border border-line bg-tint px-3 py-2 text-sm text-muted"
        >
          {strings.uploadingStatus(uploadingFilename)}
        </p>
      )}
    </form>
  )
}
