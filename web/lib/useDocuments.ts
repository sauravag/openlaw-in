import { useState } from 'react'
import { deleteDocument, uploadDocument } from './api'
import {
  attachedFromSummary,
  withDocument,
  withoutDocument,
  type AttachedDocument,
} from './documents'
import type { RefusalData } from './types'

interface UseDocumentsParams {
  /** Refusal copy and the retention notice are resolved in this language. */
  language: string
  /** The conversation uploads attach to, or `null` to let the server mint one. */
  conversationId: string | null
  /** Called with the conversation the server attached an upload to -- a first
   * upload is one of the two ways a conversation comes into existence. */
  onConversationStarted: (conversationId: string) => void
}

export interface DocumentsState {
  documents: readonly AttachedDocument[]
  /** The file currently in flight, for the progress line. */
  uploadingFilename: string | null
  /** The document currently being deleted, so its button can be disabled. */
  deletingId: string | null
  /**
   * The last upload or delete failure, as a refusal carrying helplines.
   * Rendered through `RefusalNotice` -- the same surface every other refusal
   * on this page uses -- so requirement 6 holds for the upload paths without a
   * second implementation of "a refusal must still deliver help".
   */
  refusal: RefusalData | null
  attachFile: (file: File) => Promise<void>
  removeDocument: (documentId: string) => Promise<void>
  /**
   * Adopt the documents a restored conversation still holds.
   *
   * Replaces the list outright rather than merging: the server's answer to
   * "which of my files are still attached" is the authoritative one, and a
   * merge could leave a document on screen that was deleted in another tab.
   */
  restore: (documents: readonly AttachedDocument[]) => void
  /** Forget every document, after the whole conversation has been deleted. */
  clear: () => void
}

/**
 * The attached-documents half of the page's state.
 *
 * Extracted from `app/page.tsx` so that file holds the conversation and the
 * question, and this one holds documents: upload, progress, deletion, and the
 * refusal any of those can produce. All three mutations go through
 * `lib/api.ts`'s total functions, so nothing here has a throwing path and no
 * failure can leave the page without help on it.
 *
 * Every state update is a new value (`withDocument`/`withoutDocument`), never
 * an in-place edit of the list.
 */
export function useDocuments({
  language,
  conversationId,
  onConversationStarted,
}: UseDocumentsParams): DocumentsState {
  const [documents, setDocuments] = useState<readonly AttachedDocument[]>([])
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<RefusalData | null>(null)

  async function attachFile(file: File): Promise<void> {
    setRefusal(null)
    setUploadingFilename(file.name)
    const outcome = await uploadDocument({
      file,
      conversationId,
      language,
      heldDocumentCount: documents.length,
    })
    setUploadingFilename(null)
    if (outcome.kind === 'refused') {
      setRefusal(outcome.data)
      return
    }
    setDocuments((current) => withDocument(current, attachedFromSummary(outcome.data)))
    onConversationStarted(outcome.data.conversation_id)
  }

  async function removeDocument(documentId: string): Promise<void> {
    setRefusal(null)
    setDeletingId(documentId)
    const outcome = await deleteDocument({ documentId, language })
    setDeletingId(null)
    if (outcome.kind === 'refused') {
      // The document stays on screen on purpose: showing it as gone while the
      // server still holds it would be the worse of the two lies, and this is
      // the only control a person has for withdrawing it.
      setRefusal(outcome.data)
      return
    }
    setDocuments((current) => withoutDocument(current, documentId))
  }

  function restore(restored: readonly AttachedDocument[]): void {
    setDocuments([...restored])
  }

  function clear(): void {
    setDocuments([])
    setRefusal(null)
    setDeletingId(null)
    setUploadingFilename(null)
  }

  return {
    documents,
    uploadingFilename,
    deletingId,
    refusal,
    attachFile,
    removeDocument,
    restore,
    clear,
  }
}
