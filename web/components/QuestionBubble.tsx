import type { UIStrings } from '@/lib/strings'

interface QuestionBubbleProps {
  question: string
  attachedFilenames: readonly string[]
  strings: UIStrings
}

/**
 * The person's own question, as a right-aligned bubble -- the one familiar
 * chat convention worth borrowing, because it makes "this is what I said"
 * versus "this is what came back" readable at a glance.
 *
 * The "You asked" label stays in the accessibility tree (visually hidden), so
 * a screen reader still hears whose words these are. Attached documents keep
 * the reader's-own-paperwork styling used everywhere else.
 */
export default function QuestionBubble({
  question,
  attachedFilenames,
  strings,
}: QuestionBubbleProps) {
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[85%] flex-col gap-1 rounded-2xl rounded-br-md bg-brand-soft px-4 py-3 sm:max-w-[75%]">
        <p className="sr-only">{strings.yourQuestionLabel}</p>
        <p className="text-base leading-relaxed whitespace-pre-line text-ink sm:text-lg">
          {question}
        </p>
        {attachedFilenames.length > 0 && (
          <p className="mt-1 flex flex-wrap items-baseline gap-2 text-sm text-muted">
            <span>{strings.attachedToThisTurnLabel}</span>
            {attachedFilenames.map((filename) => (
              <span
                key={filename}
                className="rounded-md border border-dashed border-paper-edge bg-paper px-2 py-0.5 break-all text-paper-ink"
              >
                {filename}
              </span>
            ))}
          </p>
        )}
      </div>
    </div>
  )
}
