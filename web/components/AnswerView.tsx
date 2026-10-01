import { getStrings } from '@/lib/strings'
import type { AnswerData } from '@/lib/types'
import CrisisAlert from './CrisisAlert'
import DocumentCitationList from './DocumentCitationList'
import HelplineList from './HelplineList'
import SourceList from './SourceList'

interface AnswerViewProps {
  data: AnswerData
  /**
   * Set `false` when the caller has already rendered this answer's crisis
   * block somewhere higher up the page. `app/page.tsx` does exactly that for
   * the newest turn: inside a thread the newest answer sits LAST, so a crisis
   * response left here would be below every earlier turn's legal analysis.
   * Defaults to `true`, so a standalone `AnswerView` still renders it and no
   * caller can lose it by forgetting a prop.
   */
  showCrisis?: boolean
}

/**
 * Renders one verified answer.
 *
 * **Order matters.** A crisis response renders above everything else, in a
 * visually distinct `role="alert"` box (`CrisisAlert`) -- a helpline printed
 * below a statutory explanation has already failed the person reading it
 * (Task 6 requirement 4). No visual change may move that block after the
 * answer.
 *
 * There is no code path here that reads
 * `ValidatedAnswer.unverified_summary`: `data.answer` is `AskResponse.answer`,
 * which `openlaw.api.schemas._render_answer` builds only from validated
 * claims (requirement 3) -- this component has no other prose field to
 * render, so it cannot reintroduce unverified text even by accident.
 *
 * Every helpline number is rendered from `data.helplines` -- structured
 * data, never scanned out of prose (Task 6 fix round, C2). On a crisis turn
 * `data.helplines` carries the category-specific numbers plus the NALSA
 * number the footer always cites, and `CrisisAlert` renders them inside the
 * alert box so the tap-to-call links sit where the box already has the
 * reader's attention. On an ordinary turn they render once, in the footer.
 *
 * The interface's own strings (heading, corpus note, unanswerable framing)
 * render in `data.language` -- the resolved language of the answer itself
 * (Task 6 review, H3) -- so nothing English gets spliced into Hindi prose.
 *
 * **Law versus framing.** `hasCitedLaw` is the dividing line. When the turn
 * produced citations, the answer gets the "What the law says" heading and
 * the sources sit directly beneath it; everything this system says *about*
 * that answer -- the framing note, the corpus snapshot, the escalation
 * footer -- is smaller, muted, and below a rule. When there are no
 * citations (an unanswerable turn, or a crisis short-circuit) that heading
 * is withheld: dressing "we found nothing" up as a statement of law would
 * be the exact overclaim the whole validation pipeline exists to prevent.
 *
 * **The reader's own document is a third thing, and the hardest to get
 * right.** `data.document_answer` and `data.document_citations` (Plan 6 Task
 * 15) arrive as separate fields from `answer`/`sources` and are rendered by a
 * separate component into a visually separate region -- warm ground, dashed
 * edge, paperclip, the sentence "This is your own document, not law.", and not
 * one link anywhere in it. This component never concatenates the two halves,
 * and it could not pass a document citation to `SourceList` even by mistake:
 * `DocumentCitationCard` has no `source_url`, so the types refuse it. See
 * `DocumentCitationList`'s docstring for the four independent signals that
 * draw the boundary, and `__tests__/AnswerView.test.tsx` for the structural
 * assertion that neither region ever contains the other's content.
 */
export default function AnswerView({ data, showCrisis = true }: AnswerViewProps) {
  const strings = getStrings(data.language)
  const hasCitedLaw = data.sources.length > 0

  return (
    <article className="flex flex-col gap-6">
      {showCrisis && data.crisis_response && (
        <CrisisAlert
          crisisResponse={data.crisis_response}
          helplines={data.helplines}
          language={data.language}
        />
      )}

      <div
        data-testid={data.is_answerable ? undefined : 'unanswerable'}
        className="flex flex-col gap-3"
      >
        {hasCitedLaw && (
          <h2 className="text-sm font-bold text-muted">
            {strings.answerHeading}
          </h2>
        )}
        <p className="text-lg leading-relaxed whitespace-pre-line text-ink">{data.answer}</p>
        {!data.is_answerable && (
          <p className="text-base leading-relaxed text-muted">{strings.unanswerableSuffix}</p>
        )}
        {hasCitedLaw && (
          <p className="border-l-2 border-line pl-3 text-sm leading-relaxed text-muted">
            {strings.answerFramingNote}
          </p>
        )}
      </div>

      <SourceList sources={data.sources} language={data.language} />

      {/* After the law and its sources, never interleaved with them: a reader
        * scanning down the page meets the cited law, the links that back it,
        * and only then their own paperwork, clearly marked as theirs. */}
      <DocumentCitationList
        documentAnswer={data.document_answer ?? null}
        citations={data.document_citations ?? []}
        language={data.language}
      />

      <footer className="flex flex-col gap-3 border-t border-line pt-4 text-sm text-muted">
        <p className="whitespace-pre-line">{data.footer}</p>
        {!data.crisis_response && <HelplineList helplines={data.helplines} />}
        <p>{strings.corpusNote(data.corpus_version)}</p>
      </footer>
    </article>
  )
}
