import { getStrings, type UIStrings } from '@/lib/strings'
import type { ThreadTurn } from '@/lib/thread'
import type { ConversationTurnCardData } from '@/lib/types'
import AnswerView from './AnswerView'
import DocumentCitationList from './DocumentCitationList'
import RefusalNotice from './RefusalNotice'

interface ConversationThreadProps {
  turns: readonly ThreadTurn[]
  /** The interface's own labels render in this language -- the one currently
   * selected, so the thread's furniture follows the reader rather than the
   * language an older turn happened to be asked in. */
  language: string
  /**
   * The turn whose crisis block the page has already rendered ABOVE the whole
   * thread. That turn's own copy is omitted here so help appears once, at the
   * top. Every OTHER turn keeps its crisis box exactly where it was: help
   * already delivered is never withdrawn by a later turn (Task 6 review, M3).
   */
  suppressCrisisForTurnId?: string | null
}

/** The person's own question, above the response it produced. */
function QuestionBlock({
  turn,
  strings,
}: {
  turn: ThreadTurn
  strings: UIStrings
}) {
  return (
    <div className="flex flex-col gap-1 border-l-4 border-brand-soft pl-3">
      <p className="text-xs font-bold tracking-wide text-muted uppercase">
        {strings.yourQuestionLabel}
      </p>
      <p className="text-lg leading-relaxed whitespace-pre-line text-ink">{turn.question}</p>
      {turn.attachedFilenames.length > 0 && (
        <p className="mt-1 flex flex-wrap items-baseline gap-2 text-sm text-muted">
          <span>{strings.attachedToThisTurnLabel}</span>
          {turn.attachedFilenames.map((filename) => (
            <span
              key={filename}
              className="rounded-md border border-dashed border-paper-edge bg-paper px-2 py-0.5 break-all text-paper-ink"
            >
              {filename}
            </span>
          ))}
        </p>
      )}
    </div>
  )
}

/**
 * A turn restored from storage after a reload.
 *
 * Rendered by hand rather than through `AnswerView` because a stored turn is
 * genuinely less than a live one: `ConversationTurnCard` carries no
 * `SourceCard`s and no corpus version, so `AnswerView` would print an empty
 * "Where this comes from" section and a corpus note with a blank version --
 * legal prose with nothing behind it, which is the one thing this product must
 * never show. `restoredTurnNote` says so in words instead, and the document
 * half still goes through `DocumentCitationList` so a restored turn cannot
 * become the one place where a quote from somebody's lease reads as law.
 */
function ReplayedTurn({
  data,
  strings,
}: {
  data: ConversationTurnCardData
  strings: UIStrings
}) {
  return (
    <article className="flex flex-col gap-3">
      <p className="text-lg leading-relaxed whitespace-pre-line text-ink">{data.answer}</p>
      <DocumentCitationList
        documentAnswer={data.document_answer}
        citations={[]}
        language={data.language}
      />
      <p className="border-t border-line pt-3 text-sm leading-relaxed text-muted">
        {strings.restoredTurnNote}
      </p>
    </article>
  )
}

/** Whichever surface this turn's outcome belongs on. */
function TurnOutcomeView({
  turn,
  strings,
  showCrisis,
}: {
  turn: ThreadTurn
  strings: UIStrings
  showCrisis: boolean
}) {
  if (turn.outcome.kind === 'answer') {
    return <AnswerView data={turn.outcome.data} showCrisis={showCrisis} />
  }
  if (turn.outcome.kind === 'refused') {
    return (
      <RefusalNotice
        data={turn.outcome.data}
        language={turn.language}
        showCrisis={showCrisis}
      />
    )
  }
  return <ReplayedTurn data={turn.outcome.data} strings={strings} />
}

/**
 * The conversation, oldest turn first.
 *
 * An `<ol>`, because the order is the meaning: a follow-up question only makes
 * sense after the one before it, and a screen reader announcing "list, 3
 * items" with positions is doing something useful here rather than decorative.
 * One `<li>` per turn, each holding the question and the response it produced.
 *
 * Deliberately not a chat: no bubbles, no avatars, no alignment flipping side
 * to side, nothing that animates in. The reader is often frightened and on a
 * cheap screen; the look to hold is a sheaf of papers on a legal aid desk, in
 * the order they were written.
 *
 * Renders nothing before the first question -- `app/page.tsx` shows the
 * welcome card in that space instead.
 */
export default function ConversationThread({
  turns,
  language,
  suppressCrisisForTurnId = null,
}: ConversationThreadProps) {
  if (turns.length === 0) {
    return null
  }
  const strings = getStrings(language)
  return (
    <ol aria-label={strings.threadHeading} className="flex flex-col gap-8">
      {turns.map((turn) => (
        <li key={turn.id} className="flex flex-col gap-4">
          <QuestionBlock turn={turn} strings={strings} />
          <TurnOutcomeView
            turn={turn}
            strings={strings}
            showCrisis={turn.id !== suppressCrisisForTurnId}
          />
        </li>
      ))}
    </ol>
  )
}
