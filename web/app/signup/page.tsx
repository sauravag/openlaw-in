import { notFound } from 'next/navigation'
import AuthHelplineBlock from '@/components/AuthHelplineBlock'
import SignInForm from '@/components/SignInForm'
import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings, type StringKey } from '@/lib/strings'

// Reads the query string on every request.
export const dynamic = 'force-dynamic'

type SearchParams = Record<string, string | string[] | undefined>

/** Which sentence each known `?error=` code speaks. Anything else is `failed`. */
const ERROR_COPY: Readonly<Record<string, StringKey>> = {
  expired: 'authLinkExpired',
  failed: 'authFailed',
  rate_limited: 'authRateLimited',
  invalid_email: 'authInvalidEmail',
  too_large: 'authRequestTooLarge',
  unavailable: 'authUnavailable',
}
const GENERIC_ERROR_COPY: StringKey = 'authFailed'

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value
}

/** The sentence for `code`; an unknown code never reaches the page. */
function errorCopy(code: string): StringKey {
  return Object.hasOwn(ERROR_COPY, code) ? ERROR_COPY[code] : GENERIC_ERROR_COPY
}

/**
 * Sign-in page (Mode B only; a 404 in Mode A, so the link Mode A removed
 * cannot come back as a live-but-empty page).
 *
 * Query contract: `?sent=1` (check your email), `?error=<code>` (localised
 * refusal), `?language=hi` (default English). Every state carries the static
 * helpline block. The `error` value selects a sentence and is never printed.
 */
export default async function SignupPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  if (!AUTH_ENABLED) notFound()
  const params = await searchParams
  const language = firstValue(params.language) === 'hi' ? 'hi' : 'en'
  const strings = getStrings(language)
  const errorCode = firstValue(params.error)
  const isSent = errorCode === undefined && firstValue(params.sent) === '1'
  const message =
    errorCode !== undefined ? strings[errorCopy(errorCode)] : isSent ? strings.authLinkSent : null
  const homeHref = language === 'hi' ? '/?language=hi' : '/'

  return (
    <main lang={language} className="mx-auto w-full max-w-xl px-4 py-8">
      <h1 className="text-2xl font-bold text-brand-ink">{strings.signInHeading}</h1>
      {message !== null && (
        <p role={errorCode !== undefined ? 'alert' : 'status'} className="mt-4 rounded-xl border-2 border-paper-edge bg-paper p-4 text-base leading-relaxed text-ink">
          {message}
        </p>
      )}
      {!isSent && (
        <div className="mt-4 flex flex-col gap-4">
          <p className="text-base leading-relaxed text-ink">{strings.signInIntro}</p>
          <SignInForm language={language} />
        </div>
      )}
      <p className="mt-6">
        <a href={homeHref} className="font-semibold text-brand underline decoration-2 underline-offset-4">
          {strings.signInContinueWithoutAccount}
        </a>
      </p>
      <AuthHelplineBlock language={language} />
    </main>
  )
}
