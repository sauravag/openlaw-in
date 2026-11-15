import { useRef, useState } from 'react'
import { askQuestion, deleteConversation, readConversation } from './api'
import { attachedFromConversationCard, type AttachedDocument } from './documents'
import type { Language } from './languages'
import { getStrings } from './strings'
import {
  appendTurn,
  forgetStoredConversationId,
  replayedTurn,
  storeConversationId,
  type ThreadTurn,
  type TurnOutcome,
} from './thread'
import type { ConversationPointer } from './useConversationPointer'
import type { DocumentsState } from './useDocuments'

export type ChatStatus = 'idle' | 'loading' | 'error'

/** The question on its way: shown as the person's bubble with a typing
 * indicator until the outcome arrives and it becomes a real turn. */
export interface PendingTurn {
  question: string
  attachedFilenames: readonly string[]
}

/** `sent`: an outcome (answer or refusal) became a turn. `failed`: the call
 * itself threw. `ignored`: nothing to send, or a send already in flight. */
export type AskResult = 'sent' | 'failed' | 'ignored'

interface UseChatParams {
  language: Language['code']
  pointer: ConversationPointer
  attachments: DocumentsState
}

export interface ChatState {
  turns: readonly ThreadTurn[]
  status: ChatStatus
  errorMessage: string | null
  pending: PendingTurn | null
  isSessionEnded: boolean
  ask: (question: string) => Promise<AskResult>
  showConversation: (id: string, turns: ThreadTurn[], documents: AttachedDocument[]) => void
  openConversation: (id: string) => Promise<void>
  deleteCurrentConversation: () => Promise<void>
  markSessionEnded: () => void
}

/**
 * The conversation half of the page: turns, the in-flight question, and the
 * error line.
 *
 * Requirement 1: every question goes through `askQuestion` (`lib/api.ts`),
 * which POSTs to `/ask` -- there is no other path to the answer pipeline.
 * `language` (requirement 2) is always the one the reader picked, never left
 * for the server to detect, and `documentIds` names only documents already
 * uploaded to this conversation; no bytes travel through `/ask`.
 *
 * `turns` and `status`/`errorMessage` are deliberately separate (Task 6
 * review, M3): a failed follow-up must never erase an already-rendered crisis
 * box. A new turn is appended; an error renders alongside what is there.
 */
export function useChat({ language, pointer, attachments }: UseChatParams): ChatState {
  const [turns, setTurns] = useState<readonly ThreadTurn[]>([])
  const [status, setStatus] = useState<ChatStatus>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingTurn | null>(null)
  const [isSessionEnded, setIsSessionEnded] = useState(false)
  const turnCounter = useRef(0)
  const isAsking = useRef(false)

  function fail(message: string): void {
    setStatus('error')
    setErrorMessage(message)
  }

  function recordTurn(asked: PendingTurn, outcome: TurnOutcome): void {
    turnCounter.current += 1
    const id = `turn-${turnCounter.current}`
    setTurns((current) => appendTurn(current, { id, ...asked, language, outcome }))
  }

  async function ask(rawQuestion: string): Promise<AskResult> {
    const question = rawQuestion.trim()
    // A ref, not `status`: two Enter presses inside one render must not both
    // pass a check against the same stale state value.
    if (!question || isAsking.current) return 'ignored'
    isAsking.current = true
    // Captured now, not read at render time: deleting a document later must
    // not rewrite what this turn says was read for it.
    const asked: PendingTurn = {
      question,
      attachedFilenames: attachments.documents.map((document) => document.filename),
    }
    setStatus('loading')
    setErrorMessage(null)
    setPending(asked)
    try {
      const outcome = await askQuestion({
        question,
        language,
        conversationId: pointer.conversationId,
        documentIds: attachments.documents.map((document) => document.id),
      })
      if (outcome.kind === 'answer' && outcome.data.conversation_id) {
        pointer.remember(outcome.data.conversation_id)
      }
      recordTurn(asked, outcome)
      setStatus('idle')
      return 'sent'
    } catch {
      // `askQuestion` is total (final review, H3): every server-side failure
      // resolves to a refusal carrying helplines. This catch is only
      // defence-in-depth against a genuinely unexpected runtime error, and it
      // leaves `turns` untouched (M3).
      fail(getStrings(language).genericError)
      return 'failed'
    } finally {
      setPending(null)
      isAsking.current = false
    }
  }

  /** Show a conversation the server gave back, with the documents it says
   * are still attached, so per-document delete survives a reload. */
  function showConversation(id: string, restored: ThreadTurn[], documents: AttachedDocument[]) {
    pointer.setConversationId(id)
    setTurns(restored)
    setIsSessionEnded(false)
    attachments.restore(documents)
  }

  /** Open one of the signed-in person's own conversations from the list. */
  async function openConversation(id: string): Promise<void> {
    const outcome = await readConversation({ conversationId: id, language })
    if (outcome.kind === 'refused') {
      fail(outcome.data.detail)
      return
    }
    showConversation(
      outcome.data.conversation_id,
      outcome.data.turns.map(replayedTurn),
      (outcome.data.documents ?? []).map(attachedFromConversationCard),
    )
    storeConversationId(outcome.data.conversation_id, true)
  }

  /**
   * Delete the conversation, its turns and every document in it -- the
   * broadest withdrawal this interface offers. Local state is cleared only
   * after the server confirms: a thread that vanished while its files were
   * still stored would be the worst possible lie to tell here.
   */
  async function deleteCurrentConversation(): Promise<void> {
    if (pointer.conversationId === null) return
    const outcome = await deleteConversation({ conversationId: pointer.conversationId, language })
    if (outcome.kind === 'refused') {
      fail(outcome.data.detail)
      return
    }
    setTurns([])
    pointer.setConversationId(null)
    forgetStoredConversationId()
    attachments.clear()
  }

  return {
    turns,
    status,
    errorMessage,
    pending,
    isSessionEnded,
    ask,
    showConversation,
    openConversation,
    deleteCurrentConversation,
    markSessionEnded: () => setIsSessionEnded(true),
  }
}
