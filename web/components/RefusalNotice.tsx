import { formatResetDate } from '@/lib/formatDate'
import { getStrings } from '@/lib/strings'
import type { RefusalData } from '@/lib/types'
import HelplineList from './HelplineList'

interface RefusalNoticeProps {
  data: RefusalData
  /** The interface's own labels render in this language (Task 6 review,
   * H3) -- independent of `data`, which carries no `language` field (the
   * 500 body has no answer to have been localised for). */
  language: string
}

/**
 * Renders a refusal: an exhausted monthly/anonymous allowance, a tripped
 * short-burst rate limit, or an unhandled server error (Task 6 review, C1 --
 * all three now share this shape). Never a bare "try again" dead end (Task 6
 * requirement 7):
 *
 * - a crisis response, when present, renders above the refusal detail in the
 *   same visually distinct alert box `AnswerView` uses (requirement 4) --
 *   someone in danger who happens to hit a rate limit or an outage must
 *   still see help first, not last;
 * - `quota_remaining`, `quota_reset_at` and `signup_url` render only when
 *   present -- a 429 sends all of them (per which limit fired), a 500 sends
 *   none, since an unhandled error has no quota decision to report;
 * - `escalation_footer` is always rendered, unconditionally;
 * - every helpline number renders from `data.helplines` -- structured data,
 *   never scanned out of prose (Task 6 fix round, C2). See `HelplineList`.
 */
export default function RefusalNotice({ data, language }: RefusalNoticeProps) {
  const strings = getStrings(language)
  return (
    <article data-testid="refusal" className="flex flex-col gap-4">
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

      <p className="text-base leading-relaxed text-slate-900">{data.detail}</p>

      {data.quota_remaining !== undefined && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm text-slate-700">
          <dt className="font-medium">{strings.quotaRemainingLabel}</dt>
          <dd>{data.quota_remaining}</dd>
          {data.quota_reset_at && (
            <>
              <dt className="font-medium">{strings.resetsOnLabel}</dt>
              <dd>{formatResetDate(data.quota_reset_at, language)}</dd>
            </>
          )}
        </dl>
      )}

      {data.signup_required && data.signup_url && (
        <a
          href={data.signup_url}
          className="text-blue-700 underline underline-offset-2 hover:text-blue-900"
        >
          {strings.signupCta}
        </a>
      )}

      <footer className="flex flex-col gap-2 border-t border-slate-200 pt-4 text-sm text-slate-600">
        <p className="whitespace-pre-line">{data.escalation_footer}</p>
        {!data.crisis_response && <HelplineList helplines={data.helplines} />}
      </footer>
    </article>
  )
}
