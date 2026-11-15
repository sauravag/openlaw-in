import { getStrings } from '@/lib/strings'

interface ExampleQuestionsProps {
  language: string
  /**
   * Called with the tapped question. The page puts it in the box and lets the
   * reader send it themselves -- deliberately NOT a one-tap submit. A mis-tap
   * on a small screen must not silently spend a free question, and seeing the
   * question land in the box teaches "this is the shape of a question that
   * works".
   */
  onPickExample: (question: string) => void
}

/** Tappable starter questions, under their heading. */
export default function ExampleQuestions({ language, onPickExample }: ExampleQuestionsProps) {
  const strings = getStrings(language)
  return (
    <div>
      <h3 className="text-base font-semibold text-ink">{strings.examplesHeading}</h3>
      <ul className="mt-3 flex flex-col gap-2">
        {strings.exampleQuestions.map((question) => (
          <li key={question}>
            <button
              type="button"
              onClick={() => onPickExample(question)}
              className="flex min-h-12 w-full items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 text-left text-base text-ink hover:border-brand hover:bg-brand-soft"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="h-4 w-4 shrink-0 text-brand"
                fill="currentColor"
              >
                <path d="M7 3.6 8.4 2.2l7.8 7.8-7.8 7.8L7 16.4 13.4 10 7 3.6Z" />
              </svg>
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
