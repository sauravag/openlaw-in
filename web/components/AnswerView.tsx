import { getStrings } from '@/lib/strings'
import type { AnswerData, SourceCard } from '@/lib/types'
import HelplineList from './HelplineList'

interface AnswerViewProps {
  data: AnswerData
}

//: Only an `https:` source is ever rendered as a clickable link (Task 6
//: review, M6) -- defence in depth, since `source_url` is corpus-controlled
//: today, not caller-supplied. A non-`https:` value still renders as plain
//: text rather than being silently dropped.
function isSafeSourceUrl(url: string): boolean {
  try {
    return new URL(url).protocol === 'https:'
  } catch {
    return false
  }
}

function SourceLink({ source }: { source: SourceCard }) {
  const label = source.title ?? source.source_url
  if (!isSafeSourceUrl(source.source_url)) {
    return <span className="break-all text-slate-700">{label}</span>
  }
  return (
    <a
      href={source.source_url}
      target="_blank"
      rel="noopener noreferrer"
      className="break-all text-blue-700 underline underline-offset-2 hover:text-blue-900"
    >
      {label}
    </a>
  )
}

/**
 * Renders one verified answer.
 *
 * Order matters: a crisis response renders above everything else, in a
 * visually distinct, `role="alert"` box -- a helpline printed below a
 * statutory explanation has already failed the person reading it (Task 6
 * requirement 4). There is no code path here that reads
 * `ValidatedAnswer.unverified_summary`: `data.answer` is `AskResponse.answer`,
 * which `openlaw.api.schemas._render_answer` builds only from validated
 * claims (requirement 3) -- this component has no other field to render, so
 * it cannot reintroduce unverified text even by accident.
 *
 * Every helpline number is rendered from `data.helplines` -- structured
 * data, never scanned out of prose (Task 6 fix round, C2). On a crisis turn
 * `data.helplines` carries the category-specific numbers plus the NALSA
 * number the footer always cites; `HelplineList` renders inside the alert
 * box so the tap-to-call links sit right where the danger box already has
 * the reader's attention. On an ordinary turn it renders once, in the
 * footer, and carries only NALSA.
 *
 * The interface's own strings (heading, corpus note, unanswerable framing)
 * render in `data.language` -- the resolved language of the answer itself
 * (Task 6 review, H3) -- so nothing English gets spliced into Hindi prose.
 */
export default function AnswerView({ data }: AnswerViewProps) {
  const strings = getStrings(data.language)
  return (
    <article className="flex flex-col gap-6">
      {data.crisis_response && (
        <section
          role="alert"
          data-testid="crisis"
          className="rounded-lg border-2 border-red-700 bg-red-50 p-4 text-red-900"
        >
          <p className="whitespace-pre-line text-lg font-semibold">{data.crisis_response}</p>
          <div className="mt-3">
            <HelplineList helplines={data.helplines} />
          </div>
        </section>
      )}

      <div data-testid={data.is_answerable ? undefined : 'unanswerable'}>
        <p className="whitespace-pre-line text-base leading-relaxed text-slate-900">
          {data.answer}
        </p>
        {!data.is_answerable && (
          <p className="mt-2 text-base leading-relaxed text-slate-700">
            {strings.unanswerableSuffix}
          </p>
        )}
      </div>

      {data.sources.length > 0 && (
        <section aria-label="Sources" className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            {strings.sourcesHeading}
          </h2>
          <ul className="flex flex-col gap-2">
            {data.sources.map((source) => (
              <li key={source.chunk_id}>
                <SourceLink source={source} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="flex flex-col gap-2 border-t border-slate-200 pt-4 text-sm text-slate-600">
        <p className="whitespace-pre-line">{data.footer}</p>
        {!data.crisis_response && <HelplineList helplines={data.helplines} />}
        <p>{strings.corpusNote(data.corpus_version)}</p>
      </footer>
    </article>
  )
}
