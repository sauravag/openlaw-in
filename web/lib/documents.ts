import { getFallbackHelplineSet } from './fallbackHelplines'
import { getStrings } from './strings'
import type { DocumentSummaryData, RefusalData } from './types'

/**
 * The client-side model of a document the reader has attached.
 *
 * **Everything here is about the reader's own paperwork, never about the
 * law.** The two are kept apart by type all the way down: nothing in this
 * module produces a `SourceCard`, and `DocumentCitationCard` has no
 * `source_url` to give one.
 *
 * The limits below mirror `openlaw.config.Settings`' defaults. The server is
 * and remains the enforcer -- a client limit can be bypassed by anyone with a
 * developer console, and `openlaw.api.artifacts` re-checks every one of them.
 * They exist here for two honest reasons: so the interface can STATE the
 * limit before a file is chosen (a refusal a person could have predicted is
 * not a failure), and so an obviously doomed upload costs nothing on a 2G
 * connection. `lib/__tests__/documents.test.ts` pins the values against the
 * Python source.
 */
export const MAX_UPLOAD_BYTES = 10_485_760

/** Mirrors `Settings.max_documents_per_conversation`. */
export const MAX_DOCUMENTS_PER_CONVERSATION = 3

/** Mirrors `Settings.anonymous_upload_ttl_hours`. */
export const ANONYMOUS_UPLOAD_TTL_HOURS = 24

/** Mirrors `openlaw.documents.ALLOWED_CONTENT_TYPES`, in declaration order.
 * No image type: there is no OCR, so a photograph of a notice is refused
 * rather than silently accepted and read as empty. */
export const ALLOWED_UPLOAD_TYPES = [
  'application/pdf',
  'text/plain',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
] as const

/** `accept` attribute for the file input -- extensions, because Android
 * file pickers honour those far more reliably than MIME types. */
export const ALLOWED_UPLOAD_EXTENSIONS = '.pdf,.txt,.docx'

const BYTES_PER_MEGABYTE = 1_048_576

/** The size cap as the whole number of megabytes the copy quotes. */
export const MAX_UPLOAD_MEGABYTES = Math.floor(MAX_UPLOAD_BYTES / BYTES_PER_MEGABYTE)

/** Where an anonymous reader goes to keep their upload. Mirrors
 * `Settings.signup_url`'s default. */
export const SIGN_IN_PATH = '/signup'

/** One attached document, as the interface needs it. */
export interface AttachedDocument {
  id: string
  filename: string
  pageCount: number
  /** The server's own retention sentence, rendered verbatim -- see
   * `DocumentSummaryData`. */
  retentionNotice: string
  expiresAt: string | null
}

/** Project an upload response onto the interface's own shape. */
export function attachedFromSummary(summary: DocumentSummaryData): AttachedDocument {
  return {
    id: summary.id,
    filename: summary.filename,
    pageCount: summary.page_count,
    retentionNotice: summary.retention_notice,
    expiresAt: summary.expires_at,
  }
}

/** Return a new list with `document` appended. Never mutates `documents`. */
export function withDocument(
  documents: readonly AttachedDocument[],
  document: AttachedDocument,
): AttachedDocument[] {
  return [...documents, document]
}

/** Return a new list without `documentId`. Never mutates `documents`. */
export function withoutDocument(
  documents: readonly AttachedDocument[],
  documentId: string,
): AttachedDocument[] {
  return documents.filter((document) => document.id !== documentId)
}

/** The upload failures this client can see for itself, before any request. */
export type LocalUploadProblem =
  | 'no_file'
  | 'too_large'
  | 'unsupported_type'
  | 'too_many_documents'

/**
 * Return the reason `file` cannot be uploaded, or `null` when it can.
 *
 * Checked in the order the server checks (`openlaw.api.artifacts`'s module
 * docstring) so the message a person gets locally is the one they would have
 * got from the server.
 */
export function localUploadProblem(
  file: File | null,
  heldDocumentCount: number,
): LocalUploadProblem | null {
  if (file === null) return 'no_file'
  if (!ALLOWED_UPLOAD_TYPES.includes(file.type as (typeof ALLOWED_UPLOAD_TYPES)[number])) {
    return 'unsupported_type'
  }
  if (file.size > MAX_UPLOAD_BYTES) return 'too_large'
  if (heldDocumentCount >= MAX_DOCUMENTS_PER_CONVERSATION) return 'too_many_documents'
  return null
}

function problemDetail(problem: LocalUploadProblem, language: string): string {
  const strings = getStrings(language)
  if (problem === 'too_large') return strings.uploadTooLargeError(MAX_UPLOAD_MEGABYTES)
  if (problem === 'unsupported_type') return strings.uploadUnsupportedTypeError
  if (problem === 'too_many_documents') {
    return strings.uploadTooManyDocumentsError(MAX_DOCUMENTS_PER_CONVERSATION)
  }
  return strings.uploadNoFileError
}

/**
 * Turn a locally detected upload problem into the same help-carrying refusal
 * shape the server sends.
 *
 * Requirement 6 says every refusal state keeps delivering help, and a failure
 * the client caught is still somebody's last screen -- the person who just
 * tried to attach a photograph of an eviction notice has not been helped by a
 * red sentence alone. The numbers come from `lib/fallbackHelplines.ts`, the
 * same static set used when the server is unreachable.
 *
 * `crisis_response` is deliberately `null`: a file that was too big is not an
 * emergency, and claiming otherwise would be the "when in doubt, shout" error
 * in the wrong direction. The helplines are present to be found; nothing
 * pretends to know the reader is in danger.
 */
export function uploadRefusal(problem: LocalUploadProblem, language: string): RefusalData {
  return {
    detail: problemDetail(problem, language),
    escalation_footer: getStrings(language).uploadFailedFooter,
    crisis_response: null,
    helplines: getFallbackHelplineSet(language).helplines,
  }
}
