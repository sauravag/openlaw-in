import type { Ref } from 'react'
import { getStrings, type UIStrings } from '@/lib/strings'
import type { ThreadTurn } from '@/lib/thread'
import type { ConversationTurnCardData } from '@/lib/types'
import AnswerView from './AnswerView'
import CrisisAlert from './CrisisAlert'
import DocumentCitationList from './DocumentCitationList'
import QuestionBubble from './QuestionBubble'
import RefusalNotice from './RefusalNotice'

interface ConversationThreadProps {
  turns: readonly ThreadTurn[]
  /** The interface's own labels render in this language -- the one currently
   * selected, so the thread's furniture follows the reader rather than the
   * language an older turn happened to be asked in. */
  language: string
  /** Attached to the newest turn's reply, so the page can scroll that reply's
   * START -- its crisis block, when there is one -- to the top of the view. */
  newestReplyRef?: Ref<HTMLDivElement>
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
 *
 * **Two things a restored turn used to lose, and must not** (final whole-plan
 * review). Its crisis block: `CrisisAlert` renders FIRST here, above the
 * answer, exactly as `AnswerView` and `RefusalNotice` render it, so a woman
 * whose phone reclaimed the tab comes back to Tele-MANAS rather than to her
 * own disclosure above a blank (HIGH-3). And its document citations, which
 * were hard-coded empty: `filename` and `page_number` are the only means she
 * has of checking a statement about her own paperwork, so a claim must never
 * outlive them (MEDIUM-2).
 */
interface ReplayedTurnProps {
  data: ConversationTurnCardData
  strings: UIStrings
}

function ReplayedTurn({ data, strings }: ReplayedTurnProps) {
  return (
    <article className="flex flex-col gap-3">
      {data.crisis_response && (
        <CrisisAlert
          crisisResponse={data.crisis_response}
          helplines={data.helplines ?? []}
          language={data.language}
        />
      )}
      <p className="text-lg leading-relaxed whitespace-pre-line text-ink">{data.answer}</p>
      <DocumentCitationList
        documentAnswer={data.document_answer}
        citations={data.document_citations ?? []}
        language={data.language}
      />
      <p className="border-t border-line pt-3 text-sm leading-relaxed text-muted">
        {strings.restoredTurnNote}
      </p>
    </article>
  )
}

interface TurnOutcomeViewProps {
  turn: ThreadTurn
  strings: UIStrings
}

/** Whichever surface this turn's outcome belongs on. Each one renders its
 * crisis block, when there is one, as its first child. */
function TurnOutcomeView({ turn, strings }: TurnOutcomeViewProps) {
  if (turn.outcome.kind === 'answer') return <AnswerView data={turn.outcome.data} />
  if (turn.outcome.kind === 'refused') {
    return <RefusalNotice data={turn.outcome.data} language={turn.language} />
  }
  return <ReplayedTurn data={turn.outcome.data} strings={strings} />
}

/**
 * The conversation, oldest turn first.
 *
 * An `<ol>`, because the order is the meaning: a follow-up only makes sense
 * after the one before it, and a screen reader announcing "list, 3 items" with
 * positions is doing something useful here rather than decorative. One `<li>`
 * per turn, each holding the question and the reply it produced.
 *
 * Laid out as a chat (the owner asked for one): the person's question is a
 * right-aligned bubble; the reply runs full width on the left with no bubble
 * around it, because legal prose with sources needs the whole reading
 * measure. Nothing animates in. The reply's own structure is unchanged:
 * crisis block first, then the law, its sources, the reader's documents.
 *
 * Renders nothing before the first question -- the page shows the welcome
 * card in that space instead.
 */
export default function ConversationThread({
  turns,
  language,
  newestReplyRef,
}: ConversationThreadProps) {
  if (turns.length === 0) {
    return null
  }
  const strings = getStrings(language)
  const newestId = turns[turns.length - 1].id
  return (
    <ol aria-label={strings.threadHeading} className="flex flex-col gap-10">
      {turns.map((turn) => (
        <li key={turn.id} className="flex flex-col gap-5">
          <QuestionBubble
            question={turn.question}
            attachedFilenames={turn.attachedFilenames}
            strings={strings}
          />
          {/* `scroll-mt-4`: scrolled to the top of the message list, the
            * crisis block keeps a little air above it. */}
          <div
            ref={turn.id === newestId ? newestReplyRef : undefined}
            className="scroll-mt-4"
          >
            <TurnOutcomeView turn={turn} strings={strings} />
          </div>
        </li>
      ))}
    </ol>
  )
}
