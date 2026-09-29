import { getStrings } from '@/lib/strings'
import type { Helpline } from '@/lib/types'
import HelplineList from './HelplineList'

interface CrisisAlertProps {
  crisisResponse: string
  helplines: Helpline[]
  /** The block's own heading renders in this language. */
  language: string
}

/**
 * The one block on this page allowed to use red.
 *
 * Extracted from `AnswerView` and `RefusalNotice`, which carried identical
 * copies (Task 6 review, C1 put the same markup in both). One component
 * means the two can no longer drift -- and drift here is the expensive kind:
 * both paths exist so that a person in danger sees help whether the server
 * answered, refused, or fell over.
 *
 * **Callers must render this FIRST, before any legal content.** A helpline
 * printed below a statutory explanation has already failed the person
 * reading it. `role="alert"` makes a screen reader announce it the moment it
 * appears, and the tap-to-call numbers come from structured `helplines`
 * data, never scanned out of the prose above them (Task 6 fix round, C2).
 *
 * The visual treatment leads with a thick red edge and an icon as well as
 * the colour: a red-on-white block is invisible as "urgent" to a reader with
 * a colour-vision deficiency, so the heading says it in words too.
 */
export default function CrisisAlert({
  crisisResponse,
  helplines,
  language,
}: CrisisAlertProps) {
  const strings = getStrings(language)
  return (
    <section
      role="alert"
      data-testid="crisis"
      className="overflow-hidden rounded-xl border border-l-[6px] border-crisis-edge bg-crisis-soft"
    >
      <div className="flex flex-col gap-3 p-4 sm:p-5">
        <h2 className="flex items-center gap-2 text-base font-bold text-crisis">
          <svg
            aria-hidden="true"
            viewBox="0 0 20 20"
            className="h-5 w-5 shrink-0"
            fill="currentColor"
          >
            <path d="M10 1.8 1 17.2h18L10 1.8Zm0 4.9a.95.95 0 0 1 .95.95v4a.95.95 0 1 1-1.9 0v-4A.95.95 0 0 1 10 6.7Zm0 9.2a1.15 1.15 0 1 1 0-2.3 1.15 1.15 0 0 1 0 2.3Z" />
          </svg>
          {strings.crisisHeading}
        </h2>
        <p className="text-lg leading-relaxed font-semibold whitespace-pre-line text-crisis">
          {crisisResponse}
        </p>
        <HelplineList helplines={helplines} tone="crisis" />
      </div>
    </section>
  )
}
