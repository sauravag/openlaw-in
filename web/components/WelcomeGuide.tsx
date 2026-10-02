import { getStrings } from '@/lib/strings'

interface WelcomeGuideProps {
  /** All copy here renders in this language. */
  language: string
  /**
   * Called with the tapped example question. The page puts it in the box
   * and lets the reader press Ask themselves -- deliberately NOT a one-tap
   * submit. A mis-tap on a small screen must not silently spend one of a
   * person's free questions, and seeing the question land in the box is
   * what teaches "this is the shape of a question that works".
   */
  onPickExample: (question: string) => void
}

/**
 * What a first-time visitor sees instead of an empty page.
 *
 * The audience is someone with no lawyer, possibly frightened, who has
 * never used a tool like this. Three things have to land before they will
 * type anything: this is free, the answers are checkable, and it is not
 * pretending to be a lawyer. Those are the three `welcomePoints`, in that
 * order, and none of them is a claim this project cannot back.
 *
 * `coverageNote` is the honest counterweight: naming what is NOT in the
 * corpus (district courts, consumer fora, anything after the snapshot) is
 * as much a part of being trustworthy as naming what is. It mirrors the
 * repository README's "Coverage limits" section rather than inventing a
 * friendlier version of it.
 *
 * Hidden as soon as there is a result -- see `app/page.tsx`. It is an empty
 * state, not a permanent banner competing with the answer.
 */
export default function WelcomeGuide({ language, onPickExample }: WelcomeGuideProps) {
  const strings = getStrings(language)
  return (
    <section
      aria-labelledby="welcome-heading"
      className="flex flex-col gap-6 rounded-2xl border border-line bg-tint p-5 sm:p-6"
    >
      <div>
        <h2 id="welcome-heading" className="text-lg font-bold text-ink">
          {strings.welcomeHeading}
        </h2>
        <ul className="mt-3 flex flex-col gap-2.5">
          {strings.welcomePoints.map((point) => (
            <li key={point} className="flex items-start gap-2.5 text-base text-ink">
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="mt-1 h-4 w-4 shrink-0 text-brand"
                fill="currentColor"
              >
                <path d="M7.6 14.3 3.3 10l1.4-1.4 2.9 2.9 7.7-7.7L16.7 5l-9.1 9.3Z" />
              </svg>
              {point}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-sm leading-relaxed text-muted">{strings.coverageNote}</p>
      </div>

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
    </section>
  )
}
