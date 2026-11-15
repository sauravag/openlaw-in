import { useRef, type ReactNode, type RefObject } from 'react'
import ConversationThread from '@/components/ConversationThread'
import PendingReply from '@/components/PendingReply'
import RefusalNotice from '@/components/RefusalNotice'
import WelcomeGuide from '@/components/WelcomeGuide'
import { useScrollIntoViewOnChange } from '@/lib/pageEffects'
import { getStrings } from '@/lib/strings'
import { latestTurn, turnCrisis, type ThreadTurn } from '@/lib/thread'
import type { RefusalData } from '@/lib/types'
import type { ChatStatus, PendingTurn } from '@/lib/useChat'

interface MessageListProps {
  language: string
  /** Banners that belong above the conversation (misconfiguration, redirect
   * outcomes, session ended, no-JS notice). */
  notices: ReactNode
  turns: readonly ThreadTurn[]
  pending: PendingTurn | null
  status: ChatStatus
  errorMessage: string | null
  uploadRefusal: RefusalData | null
  canDeleteConversation: boolean
  onDeleteConversation: () => void
  /** Mobile empty state: open the sheet that holds the example questions. */
  onShowExamples: () => void
}

/**
 * The polite live-region text for the newest turn. A reply that just
 * arrived is announced, and says so when it carries urgent help (review M1);
 * a conversation restored after a reload is not announced.
 */
function replyAnnouncement(turns: readonly ThreadTurn[], language: string): string {
  const newest = latestTurn(turns)
  if (newest === null || newest.outcome.kind === 'replay') return ''
  const strings = getStrings(language)
  return turnCrisis(newest) === null
    ? strings.replyArrivedAnnouncement(turns.length)
    : strings.replyWithHelpAnnouncement(turns.length)
}

interface MessageScrollRefs {
  pendingRef: RefObject<HTMLDivElement | null>
  newestReplyRef: RefObject<HTMLDivElement | null>
  errorRef: RefObject<HTMLParagraphElement | null>
  refusalRef: RefObject<HTMLDivElement | null>
}

/**
 * Sending brings the person's own question into view; a reply arriving
 * scrolls the START of that reply -- its crisis block first, when there is
 * one -- to the top, never to the bottom, which could be past the help.
 * Nothing else scrolls the list, so reading back through history is never
 * interrupted. See `useScrollIntoViewOnChange`.
 */
function useMessageScroll(
  turns: readonly ThreadTurn[],
  pending: PendingTurn | null,
  errorKey: string | null,
  uploadRefusal: RefusalData | null,
): MessageScrollRefs {
  const pendingRef = useRef<HTMLDivElement>(null)
  const newestReplyRef = useRef<HTMLDivElement>(null)
  const errorRef = useRef<HTMLParagraphElement>(null)
  const refusalRef = useRef<HTMLDivElement>(null)
  useScrollIntoViewOnChange(pendingRef, pending, 'end')
  useScrollIntoViewOnChange(newestReplyRef, latestTurn(turns)?.id ?? null, 'start')
  useScrollIntoViewOnChange(errorRef, errorKey, 'nearest')
  useScrollIntoViewOnChange(refusalRef, uploadRefusal, 'nearest')
  return { pendingRef, newestReplyRef, errorRef, refusalRef }
}

interface EmptyStateProps {
  language: string
  onShowExamples: () => void
}

function EmptyState({ language, onShowExamples }: EmptyStateProps) {
  return (
    <div className="flex flex-1 flex-col justify-center gap-3 py-4">
      {/* The examples live in the side pane; on a phone that pane is a sheet. */}
      <WelcomeGuide language={language} />
      <button
        type="button"
        onClick={onShowExamples}
        className="min-h-12 self-center rounded-xl border-2 border-brand px-5 text-base font-semibold text-brand lg:hidden"
      >
        {getStrings(language).showExamplesButton}
      </button>
    </div>
  )
}

interface ErrorLineProps {
  message: string
  hasTurns: boolean
  language: string
  ref: RefObject<HTMLParagraphElement | null>
}

function ErrorLine({ message, hasTurns, language, ref }: ErrorLineProps) {
  return (
    <p
      ref={ref}
      role="alert"
      className="rounded-xl border-2 border-crisis-edge bg-crisis-soft p-4 text-base text-crisis"
    >
      {message}
      {hasTurns && (
        <span className="mt-1 block text-sm">{getStrings(language).previousAnswerNotice}</span>
      )}
    </p>
  )
}

interface DeleteConversationProps {
  language: string
  onDelete: () => void
}

function DeleteConversationButton({ language, onDelete }: DeleteConversationProps) {
  return (
    <button
      type="button"
      onClick={onDelete}
      className="min-h-12 self-start rounded-xl border border-line px-4 text-sm font-semibold text-muted hover:bg-tint"
    >
      {getStrings(language).deleteConversationLabel}
    </button>
  )
}

/**
 * The scrolling middle of the chat: notices, the welcome card or the thread,
 * the pending reply, and any error -- oldest at the top.
 *
 * Announcing: focus does not move onto a reply (it stays in, or on a phone
 * leaves, the composer), so a new reply is announced through the
 * `aria-live="polite"` region below, as the single-answer page's focus move
 * used to. `CrisisAlert` keeps its own `role="alert"`, so a crisis block is
 * still announced assertively, and the polite line then says help is in it.
 */
export default function MessageList(props: MessageListProps) {
  const { language, turns, pending, uploadRefusal } = props
  const errorKey = props.status === 'error' ? props.errorMessage : null
  const { pendingRef, newestReplyRef, errorRef, refusalRef } = useMessageScroll(
    turns,
    pending,
    errorKey,
    uploadRefusal,
  )
  return (
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-6 px-4 py-6">
        {props.notices}
        {turns.length === 0 && pending === null && (
          <EmptyState language={language} onShowExamples={props.onShowExamples} />
        )}
        <ConversationThread turns={turns} language={language} newestReplyRef={newestReplyRef} />
        {pending && <PendingReply ref={pendingRef} pending={pending} language={language} />}
        {errorKey && (
          <ErrorLine ref={errorRef} message={errorKey} hasTurns={turns.length > 0} language={language} />
        )}
        {uploadRefusal && (
          // Every upload and delete failure renders through the same surface
          // as every other refusal: escalation footer and helplines attached.
          <div ref={refusalRef}>
            <RefusalNotice data={uploadRefusal} language={language} />
          </div>
        )}
        {props.canDeleteConversation && (
          <DeleteConversationButton language={language} onDelete={props.onDeleteConversation} />
        )}
        <p aria-live="polite" aria-atomic="true" data-testid="reply-announcer" className="sr-only">
          {replyAnnouncement(turns, language)}
        </p>
      </div>
    </div>
  )
}
