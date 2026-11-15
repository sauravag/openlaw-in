import { useRef, type ReactNode } from 'react'
import ConversationThread from '@/components/ConversationThread'
import PendingReply from '@/components/PendingReply'
import RefusalNotice from '@/components/RefusalNotice'
import WelcomeGuide from '@/components/WelcomeGuide'
import { useScrollIntoViewOnChange } from '@/lib/pageEffects'
import { getStrings } from '@/lib/strings'
import { latestTurn, type ThreadTurn } from '@/lib/thread'
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

/** The polite live-region text for the newest turn: a reply that just
 * arrived is announced; a conversation restored after a reload is not. */
function replyAnnouncement(turns: readonly ThreadTurn[], language: string): string {
  const newest = latestTurn(turns)
  if (newest === null || newest.outcome.kind === 'replay') return ''
  return getStrings(language).replyArrivedAnnouncement(turns.length)
}

interface EmptyStateProps {
  language: string
  onShowExamples: () => void
}

function EmptyState({ language, onShowExamples }: EmptyStateProps) {
  const strings = getStrings(language)
  return (
    <div className="flex flex-1 flex-col justify-center gap-3 py-4">
      {/* The examples live in the side pane; on a phone that pane is a sheet. */}
      <WelcomeGuide language={language} onPickExample={() => undefined} showExamples={false} />
      <button
        type="button"
        onClick={onShowExamples}
        className="min-h-12 self-center rounded-xl border-2 border-brand px-5 text-base font-semibold text-brand lg:hidden"
      >
        {strings.showExamplesButton}
      </button>
    </div>
  )
}

interface ErrorLineProps {
  message: string
  hasTurns: boolean
  language: string
}

function ErrorLine({ message, hasTurns, language }: ErrorLineProps) {
  return (
    <>
      {message}
      {hasTurns && (
        <span className="mt-1 block text-sm">{getStrings(language).previousAnswerNotice}</span>
      )}
    </>
  )
}

/**
 * The scrolling middle of the chat: notices, the welcome card or the thread,
 * the pending reply, and any error -- oldest at the top.
 *
 * Scrolling (see `useScrollIntoViewOnChange`): sending brings the person's
 * own question into view; a reply arriving scrolls the START of that reply,
 * whose first child is the crisis block when there is one, to the top --
 * never to the bottom, which could be past the help. Nothing else scrolls the
 * list, so reading back through history is never interrupted.
 *
 * Announcing: focus stays in the composer, so a new reply is announced
 * through the `aria-live="polite"` region below rather than by moving focus
 * onto it, as the single-answer page used to. `CrisisAlert` keeps its own
 * `role="alert"`, so a crisis block is still announced assertively.
 */
export default function MessageList(props: MessageListProps) {
  const { language, turns, pending, status, errorMessage, uploadRefusal } = props
  const strings = getStrings(language)
  const pendingRef = useRef<HTMLDivElement>(null)
  const newestReplyRef = useRef<HTMLDivElement>(null)
  const errorRef = useRef<HTMLParagraphElement>(null)
  const refusalRef = useRef<HTMLDivElement>(null)
  const errorKey = status === 'error' ? errorMessage : null
  useScrollIntoViewOnChange(pendingRef, pending, 'end')
  useScrollIntoViewOnChange(newestReplyRef, latestTurn(turns)?.id ?? null, 'start')
  useScrollIntoViewOnChange(errorRef, errorKey, 'nearest')
  useScrollIntoViewOnChange(refusalRef, uploadRefusal, 'nearest')
  const isEmpty = turns.length === 0 && pending === null

  return (
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-6 px-4 py-6">
        {props.notices}
        {isEmpty && <EmptyState language={language} onShowExamples={props.onShowExamples} />}
        <ConversationThread turns={turns} language={language} newestReplyRef={newestReplyRef} />
        {pending && <PendingReply ref={pendingRef} pending={pending} language={language} />}
        {errorKey && (
          <p
            ref={errorRef}
            role="alert"
            className="rounded-xl border-2 border-crisis-edge bg-crisis-soft p-4 text-base text-crisis"
          >
            <ErrorLine message={errorKey} hasTurns={turns.length > 0} language={language} />
          </p>
        )}
        {uploadRefusal && (
          // Every upload and delete failure renders through the same surface
          // as every other refusal, so it arrives with the escalation footer
          // and tap-to-call helplines attached (requirement 6).
          <div ref={refusalRef}>
            <RefusalNotice data={uploadRefusal} language={language} />
          </div>
        )}
        {props.canDeleteConversation && (
          <button
            type="button"
            onClick={props.onDeleteConversation}
            className="min-h-12 self-start rounded-xl border border-line px-4 text-sm font-semibold text-muted hover:bg-tint"
          >
            {strings.deleteConversationLabel}
          </button>
        )}
        <p aria-live="polite" aria-atomic="true" data-testid="reply-announcer" className="sr-only">
          {replyAnnouncement(turns, language)}
        </p>
      </div>
    </div>
  )
}
