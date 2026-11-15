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

/** The chat's raw state and the two updates every action shares. */
interface TurnState {
  turns: readonly ThreadTurn[]
  setTurns: (turns: readonly ThreadTurn[]) => void
  status: ChatStatus
  setStatus: (status: ChatStatus) => void
  errorMessage: string | null
  setErrorMessage: (message: string | null) => void
  pending: PendingTurn | null
  setPending: (pending: PendingTurn | null) => void
  isSessionEnded: boolean
  setIsSessionEnded: (isEnded: boolean) => void
  fail: (message: string) => void
  recordTurn: (asked: PendingTurn, outcome: TurnOutcome, language: string) => void
}

function useTurnState(): TurnState {
  const [turns, setTurns] = useState<readonly ThreadTurn[]>([])
  const [status, setStatus] = useState<ChatStatus>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingTurn | null>(null)
  const [isSessionEnded, setIsSessionEnded] = useState(false)
  const turnCounter = useRef(0)

  function fail(message: string): void {
    setStatus('error')
    setErrorMessage(message)
  }

  function recordTurn(asked: PendingTurn, outcome: TurnOutcome, language: string): void {
    turnCounter.current += 1
    const id = `turn-${turnCounter.current}`
    setTurns((current) => appendTurn(current, { id, ...asked, language, outcome }))
  }

  return {
    turns, setTurns, status, setStatus, errorMessage, setErrorMessage, pending, setPending,
    isSessionEnded, setIsSessionEnded, fail, recordTurn,
  }
}

/** Sending one question. See `useChat` for the rules it keeps. */
function useAsk(state: TurnState, { language, pointer, attachments }: UseChatParams) {
  const isAsking = useRef(false)

  return async function ask(rawQuestion: string): Promise<AskResult> {
    const question = rawQuestion.trim()
    // A ref, not `status`: two Enter presses inside one render must not both
    // pass a check against the same stale state value.
    if (!question || isAsking.current) return 'ignored'
    isAsking.current = true
    // Captured now, not read at render time: deleting a document later must
    // not rewrite what this turn says was read for it.
    const filenames = attachments.documents.map((document) => document.filename)
    const asked: PendingTurn = { question, attachedFilenames: filenames }
    state.setStatus('loading')
    state.setErrorMessage(null)
    state.setPending(asked)
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
      state.recordTurn(asked, outcome, language)
      state.setStatus('idle')
      return 'sent'
    } catch {
      // `askQuestion` is total (final review, H3): every server-side failure
      // resolves to a refusal carrying helplines. This catch is only
      // defence-in-depth, and it leaves `turns` untouched (M3).
      state.fail(getStrings(language).genericError)
      return 'failed'
    } finally {
      state.setPending(null)
      isAsking.current = false
    }
  }
}

/** Showing, opening and deleting whole conversations. */
function useConversationActions(state: TurnState, { language, pointer, attachments }: UseChatParams) {
  /** Show a conversation the server gave back, with the documents it says
   * are still attached, so per-document delete survives a reload. */
  function showConversation(id: string, restored: ThreadTurn[], documents: AttachedDocument[]) {
    pointer.setConversationId(id)
    state.setTurns(restored)
    state.setIsSessionEnded(false)
    attachments.restore(documents)
  }

  /** Open one of the signed-in person's own conversations from the list. */
  async function openConversation(id: string): Promise<void> {
    const outcome = await readConversation({ conversationId: id, language })
    if (outcome.kind === 'refused') return state.fail(outcome.data.detail)
    const { conversation_id: conversationId, turns, documents } = outcome.data
    showConversation(
      conversationId,
      turns.map(replayedTurn),
      (documents ?? []).map(attachedFromConversationCard),
    )
    storeConversationId(conversationId, true)
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
    if (outcome.kind === 'refused') return state.fail(outcome.data.detail)
    state.setTurns([])
    pointer.setConversationId(null)
    forgetStoredConversationId()
    attachments.clear()
  }

  return { showConversation, openConversation, deleteCurrentConversation }
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
export function useChat(params: UseChatParams): ChatState {
  const state = useTurnState()
  const ask = useAsk(state, params)
  const actions = useConversationActions(state, params)
  return {
    turns: state.turns,
    status: state.status,
    errorMessage: state.errorMessage,
    pending: state.pending,
    isSessionEnded: state.isSessionEnded,
    ask,
    ...actions,
    markSessionEnded: () => state.setIsSessionEnded(true),
  }
}
