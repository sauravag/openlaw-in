import PhoneLinkedText from './PhoneLinkedText'
import type { AnswerData } from '@/lib/types'

interface AnswerViewProps {
  data: AnswerData
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
 */
export default function AnswerView({ data }: AnswerViewProps) {
  return (
    <article className="flex flex-col gap-6">
      {data.crisis_response && (
        <section
          role="alert"
          data-testid="crisis"
          className="rounded-lg border-2 border-red-700 bg-red-50 p-4 text-red-900"
        >
          <p className="whitespace-pre-line text-lg font-semibold">
            <PhoneLinkedText text={data.crisis_response} />
          </p>
        </section>
      )}

      {data.is_answerable ? (
        <p className="whitespace-pre-line text-base leading-relaxed text-slate-900">
          {data.answer}
        </p>
      ) : (
        <p
          data-testid="unanswerable"
          className="whitespace-pre-line text-base leading-relaxed text-slate-900"
        >
          {data.answer} You can still get help from a free legal aid lawyer.
        </p>
      )}

      {data.sources.length > 0 && (
        <section aria-label="Sources" className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Where this comes from
          </h2>
          <ul className="flex flex-col gap-2">
            {data.sources.map((source) => (
              <li key={source.chunk_id}>
                <a
                  href={source.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-700 underline underline-offset-2 hover:text-blue-900"
                >
                  {source.title ?? source.source_url}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="flex flex-col gap-1 border-t border-slate-200 pt-4 text-sm text-slate-600">
        <p className="whitespace-pre-line">
          <PhoneLinkedText text={data.footer} />
        </p>
        <p>
          Based on the {data.corpus_version} snapshot of Indian law. Always
          check the official source before relying on this.
        </p>
      </footer>
    </article>
  )
}
