import type { Helpline } from '@/lib/types'

type HelplineTone = 'calm' | 'crisis'

interface HelplineListProps {
  helplines: Helpline[]
  /**
   * `calm` for the standing card at the top of the page, `crisis` for the
   * red alert block. Only the colour differs -- the markup, the accessible
   * name and the `tel:` href are identical, because the number has to work
   * the same either way.
   */
  tone?: HelplineTone
}

const TONE_CLASSES: Record<HelplineTone, string> = {
  calm: 'border-brand-soft bg-white text-brand-ink hover:border-brand hover:bg-brand-soft',
  crisis: 'border-crisis-edge/40 bg-white text-crisis',
}

/**
 * Renders each helpline as an unambiguous, one-tap `tel:` link.
 *
 * Task 6 fix round, C2: replaces `PhoneLinkedText` (removed), which scanned
 * `crisis_response`/`footer` prose for a fixed, hand-copied allowlist of
 * digit sequences. These numbers and labels come straight from the API's
 * structured `helplines` field (`openlaw.answers.escalation.Helpline`), so
 * there is no client-side text parsing involved -- this keeps working
 * correctly no matter how the surrounding prose is localised. A Hindi
 * crisis message rendered with Devanagari digits would defeat a regex; it
 * cannot defeat a field the server already resolved to a plain ASCII
 * `number` string.
 *
 * Laid out as a two-column grid of pills rather than a bulleted list. Six
 * numbers as full-width rows cost roughly 500px on a 390px-wide phone and
 * pushed the question box off the first screen; two columns halve that
 * while keeping every pill at least 56px tall, which is the size a thumb
 * actually hits. The number is the largest thing in the pill because
 * dialling it is the only thing anyone does here.
 *
 * The visible text stays `Label: Number` -- one string, with the label
 * first -- so a screen reader reads the purpose before the digits and
 * never announces a bare number.
 *
 * This renders in the server HTML with no JavaScript involved (Task 6 final
 * review, H6): no hooks, no client directive, nothing behind React state.
 */
export default function HelplineList({ helplines, tone = 'calm' }: HelplineListProps) {
  if (helplines.length === 0) {
    return null
  }
  return (
    <ul className="grid grid-cols-2 gap-2">
      {helplines.map((helpline) => (
        <li key={helpline.number}>
          <a
            href={`tel:${helpline.number}`}
            className={`flex h-full min-h-14 flex-col justify-center rounded-lg border-2 px-3 py-2 no-underline ${TONE_CLASSES[tone]}`}
          >
            <span className="text-xs leading-snug font-medium">{helpline.label}:{' '}</span>
            <span className="flex items-center gap-1.5 text-lg font-bold tabular-nums">
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="h-4 w-4 shrink-0"
                fill="currentColor"
              >
                <path d="M6.6 2.3a1.3 1.3 0 0 0-1.7-.1L3.5 3.4C2.7 4.1 2.5 5.3 3 6.4a19.4 19.4 0 0 0 10.6 10.6c1.1.5 2.3.3 3-.5l1.2-1.4a1.3 1.3 0 0 0-.1-1.7l-2.2-2a1.3 1.3 0 0 0-1.7 0l-1 .8a15.6 15.6 0 0 1-5-5l.8-1a1.3 1.3 0 0 0 0-1.7l-2-2.2Z" />
              </svg>
              {helpline.number}
            </span>
          </a>
        </li>
      ))}
    </ul>
  )
}
