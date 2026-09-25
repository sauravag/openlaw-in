import type { Helpline } from '@/lib/types'

interface HelplineListProps {
  helplines: Helpline[]
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
 */
export default function HelplineList({ helplines }: HelplineListProps) {
  if (helplines.length === 0) {
    return null
  }
  return (
    <ul className="flex flex-col gap-1">
      {helplines.map((helpline) => (
        <li key={helpline.number}>
          <a
            href={`tel:${helpline.number}`}
            className="font-semibold underline underline-offset-2"
          >
            {helpline.label}: {helpline.number}
          </a>
        </li>
      ))}
    </ul>
  )
}
