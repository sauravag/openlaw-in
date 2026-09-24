import PhoneLinkedText from './PhoneLinkedText'
import type { RefusalData } from '@/lib/types'

interface RefusalNoticeProps {
  data: RefusalData
}

/**
 * Renders a refusal: an exhausted monthly/anonymous allowance, or a tripped
 * short-burst rate limit (`QuotaExceeded` -- both HTTP 429 causes share this
 * shape). Never a bare "try again" dead end (Task 6 requirement 7):
 *
 * - a crisis response, when present, renders above the refusal detail in the
 *   same visually distinct alert box `AnswerView` uses (requirement 4) --
 *   someone in danger who happens to hit a rate limit must still see help
 *   first, not last;
 * - `quota_remaining`, `quota_reset_at` (only present for a quota refusal,
 *   never a rate limit -- see `schemas.QuotaExceeded`'s docstring) and
 *   `signup_url` are all surfaced when the API sends them;
 * - `escalation_footer` is always rendered, unconditionally.
 */
export default function RefusalNotice({ data }: RefusalNoticeProps) {
  return (
    <article data-testid="refusal" className="flex flex-col gap-4">
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

      <p className="text-base leading-relaxed text-slate-900">{data.detail}</p>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm text-slate-700">
        <dt className="font-medium">Free questions left</dt>
        <dd>{data.quota_remaining}</dd>
        {data.quota_reset_at && (
          <>
            <dt className="font-medium">Resets on</dt>
            <dd>{data.quota_reset_at}</dd>
          </>
        )}
      </dl>

      {data.signup_required && data.signup_url && (
        <a
          href={data.signup_url}
          className="text-blue-700 underline underline-offset-2 hover:text-blue-900"
        >
          Create a free account for more questions
        </a>
      )}

      <footer className="border-t border-slate-200 pt-4 text-sm text-slate-600">
        <p className="whitespace-pre-line">
          <PhoneLinkedText text={data.escalation_footer} />
        </p>
      </footer>
    </article>
  )
}
