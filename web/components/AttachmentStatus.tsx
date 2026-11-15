import DocumentList from '@/components/DocumentList'
import RetentionNotice from '@/components/RetentionNotice'
import { MAX_DOCUMENTS_PER_CONVERSATION, type AttachedDocument, type Retention } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

interface AttachmentStatusProps {
  language: string
  documents: readonly AttachedDocument[]
  deletingId: string | null
  uploadingFilename: string | null
  retention: Retention
  canOfferSignIn: boolean
  onDelete: (documentId: string) => void
}

/** True once there is anything attached or on its way -- the point from
 * which the retention promise must stay on screen above the box. */
export function hasAttachmentActivity(
  documents: readonly AttachedDocument[],
  uploadingFilename: string | null,
): boolean {
  return documents.length > 0 || uploadingFilename !== null
}

/**
 * What is attached to the next question, directly above the question box:
 * removable chips, the progress line, and the promises about the files.
 *
 * The retention notice is a promise about somebody's FIR or lease, so from
 * the moment a file is on its way it lives here, always visible, rather than
 * inside the attach tray that closes. (Before that, the tray shows it, where
 * it is read before a file is chosen.) The cap note likewise replaces the
 * paperclip once the conversation is full.
 *
 * This survives a reload: `GET /conversations/{id}` returns `documents`
 * (metadata only) and `useRestoreConversation` adopts them, which is what
 * keeps per-document deletion reachable after a refresh.
 */
export default function AttachmentStatus(props: AttachmentStatusProps) {
  const { language, documents, uploadingFilename } = props
  if (!hasAttachmentActivity(documents, uploadingFilename)) return null
  const strings = getStrings(language)
  const isFull = documents.length >= MAX_DOCUMENTS_PER_CONVERSATION
  return (
    <section
      aria-label={strings.documentsHeading}
      className="flex flex-col gap-1.5 rounded-2xl border border-dashed border-paper-edge/60 bg-paper/60 p-2.5"
    >
      <DocumentList
        documents={documents}
        language={language}
        onDelete={props.onDelete}
        deletingId={props.deletingId}
      />
      {uploadingFilename !== null && (
        <p role="status" className="text-sm text-muted">
          {strings.uploadingStatus(uploadingFilename)}
        </p>
      )}
      <RetentionNotice
        language={language}
        retention={props.retention}
        canOfferSignIn={props.canOfferSignIn}
      />
      {isFull && (
        <p className="text-sm font-semibold text-paper-ink">
          {strings.uploadFullNote(MAX_DOCUMENTS_PER_CONVERSATION)}
        </p>
      )}
    </section>
  )
}
