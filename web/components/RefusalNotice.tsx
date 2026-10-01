import { formatResetDate } from '@/lib/formatDate'
import { getStrings } from '@/lib/strings'
import type { RefusalData } from '@/lib/types'
import CrisisAlert from './CrisisAlert'
import HelplineList from './HelplineList'

interface RefusalNoticeProps {
  data: RefusalData
  /** The interface's own labels render in this language (Task 6 review,
   * H3) -- independent of `data`, which carries no `language` field (the
   * 500 body has no answer to have been localised for). */
  language: string
  /** Set `false` when the caller has already rendered this refusal's crisis
   * block higher up the page -- see `AnswerView`'s identical prop, and
   * `app/page.tsx`, which hoists the newest turn's crisis above the thread.
   * Defaults to `true` so nothing is lost by omission. */
  showCrisis?: boolean
}

/**
 * Renders a refusal: an exhausted monthly/anonymous allowance, a tripped
 * short-burst rate limit, or an unhandled server error (Task 6 review, C1 --
 * all three now share this shape). Never a bare "try again" dead end (Task 6
 * requirement 7):
 *
 * - a crisis response, when present, renders above the refusal detail in the
 *   same `CrisisAlert` box `AnswerView` uses (requirement 4) -- someone in
 *   danger who happens to hit a rate limit or an outage must still see help
 *   first, not last;
 * - `quota_remaining`, `quota_reset_at` and `signup_url` render only when
 *   present -- a 429 sends all of them (per which limit fired), a 500 sends
 *   none, since an unhandled error has no quota decision to report;
 * - `escalation_footer` is always rendered, unconditionally;
 * - every helpline number renders from `data.helplines` -- structured data,
 *   never scanned out of prose (Task 6 fix round, C2). See `HelplineList`.
 *
 * This is the surface for all six no-server-cooperation states: quota
 * refusal, rate limit, auth failure, 500, timeout, and a 200 with a
 * malformed body (`lib/api.ts` funnels every one of them here, the last
 * three via `fallbackRefusalData`). The styling is deliberately not
 * alarming -- a quiet grey card, with red left entirely to `CrisisAlert` --
 * because "we could not answer" is not itself an emergency, while what the
 * person asked about might be.
 */
export default function RefusalNotice({
  data,
  language,
  showCrisis = true,
}: RefusalNoticeProps) {
  const strings = getStrings(language)
  return (
    <article data-testid="refusal" className="flex flex-col gap-5">
      {showCrisis && data.crisis_response && (
        <CrisisAlert
          crisisResponse={data.crisis_response}
          helplines={data.helplines}
          language={language}
        />
      )}

      <div className="flex flex-col gap-4 rounded-xl border border-line bg-tint p-4 sm:p-5">
        <p className="text-base leading-relaxed text-ink">{data.detail}</p>

        {data.quota_remaining !== undefined && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm text-muted">
            <dt className="font-semibold">{strings.quotaRemainingLabel}</dt>
            <dd className="tabular-nums">{data.quota_remaining}</dd>
            {data.quota_reset_at && (
              <>
                <dt className="font-semibold">{strings.resetsOnLabel}</dt>
                <dd>{formatResetDate(data.quota_reset_at, language)}</dd>
              </>
            )}
          </dl>
        )}

        {data.signup_required && data.signup_url && (
          <a
            href={data.signup_url}
            className="inline-flex min-h-12 items-center justify-center self-start rounded-lg border-2 border-brand px-4 py-2 font-semibold text-brand no-underline hover:bg-brand-soft"
          >
            {strings.signupCta}
          </a>
        )}
      </div>

      <footer className="flex flex-col gap-3 border-t border-line pt-4 text-sm text-muted">
        <p className="whitespace-pre-line">{data.escalation_footer}</p>
        {!data.crisis_response && <HelplineList helplines={data.helplines} />}
      </footer>
    </article>
  )
}
