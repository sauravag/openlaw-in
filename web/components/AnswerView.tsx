import { getStrings } from '@/lib/strings'
import type { AnswerData } from '@/lib/types'
import CrisisAlert from './CrisisAlert'
import HelplineList from './HelplineList'
import SourceList from './SourceList'

interface AnswerViewProps {
  data: AnswerData
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
 */
export default function AnswerView({ data }: AnswerViewProps) {
  const strings = getStrings(data.language)
  const hasCitedLaw = data.sources.length > 0

  return (
    <article className="flex flex-col gap-6">
      {data.crisis_response && (
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

      <footer className="flex flex-col gap-3 border-t border-line pt-4 text-sm text-muted">
        <p className="whitespace-pre-line">{data.footer}</p>
        {!data.crisis_response && <HelplineList helplines={data.helplines} />}
        <p>{strings.corpusNote(data.corpus_version)}</p>
      </footer>
    </article>
  )
}
