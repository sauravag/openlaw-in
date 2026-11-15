import { useState, type KeyboardEvent } from 'react'
import DocumentUpload from '@/components/DocumentUpload'
import { MAX_DOCUMENTS_PER_CONVERSATION, type Retention } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

interface AttachmentTrayProps {
  language: string
  conversationId: string | null
  retention: Retention
  canOfferSignIn: boolean
  documentCount: number
  uploadingFilename: string | null
  /** True when the composer already shows the retention notice above the
   * box, so the tray does not repeat it. */
  isNoticeShownAbove: boolean
  onFileChosen: (file: File) => void
}

function PaperclipIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="currentColor">
      <path d="M13.6 4.2a2.6 2.6 0 0 0-3.7 0L4.6 9.5a4 4 0 0 0 5.7 5.7l4.6-4.6 1.4 1.4-4.6 4.6a6 6 0 0 1-8.5-8.5l5.3-5.3a4.6 4.6 0 0 1 6.5 6.5l-5.1 5.1a3.2 3.2 0 0 1-4.5-4.5l4.7-4.7 1.4 1.4-4.7 4.7a1.2 1.2 0 0 0 1.7 1.7l5.1-5.1a2.6 2.6 0 0 0 0-3.7Z" />
    </svg>
  )
}

/**
 * The paperclip, and the attach tray it opens above the question box.
 *
 * A native `<details>`, so the tray opens with JavaScript off too, and the
 * `DocumentUpload` inside it is still a real multipart form that stores the
 * file without scripts. With scripts on, choosing a file uploads it at once
 * and closes the tray; progress, chips and the retention promise then show
 * above the box (`AttachmentStatus`). The tray states the limits and, before
 * anything is attached, the retention promise -- before a file is chosen.
 *
 * Gone once the conversation holds its maximum; `AttachmentStatus` says so.
 */
export default function AttachmentTray(props: AttachmentTrayProps) {
  const [isOpen, setIsOpen] = useState(false)
  if (props.documentCount >= MAX_DOCUMENTS_PER_CONVERSATION) return null
  const strings = getStrings(props.language)

  function handleKeyDown(event: KeyboardEvent<HTMLDetailsElement>) {
    if (event.key === 'Escape' && isOpen) setIsOpen(false)
  }

  return (
    <details
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
      onKeyDown={handleKeyDown}
      className="shrink-0"
    >
      <summary
        title={strings.attachDocumentButton}
        className="flex h-12 w-12 cursor-pointer list-none items-center justify-center rounded-xl text-muted hover:bg-tint hover:text-brand [&::-webkit-details-marker]:hidden"
      >
        <PaperclipIcon />
        <span className="sr-only">{strings.attachDocumentButton}</span>
      </summary>
      <div className="absolute right-0 bottom-full left-0 z-20 mb-2 max-h-[60dvh] overflow-y-auto rounded-2xl border border-line bg-white p-4 shadow-lg">
        <DocumentUpload
          language={props.language}
          conversationId={props.conversationId}
          retention={props.retention}
          canOfferSignIn={props.canOfferSignIn}
          isUploading={props.uploadingFilename !== null}
          uploadingFilename={props.uploadingFilename}
          documentCount={props.documentCount}
          showRetentionNotice={!props.isNoticeShownAbove}
          showProgress={false}
          onFileChosen={(file) => {
            setIsOpen(false)
            props.onFileChosen(file)
          }}
        />
      </div>
    </details>
  )
}
