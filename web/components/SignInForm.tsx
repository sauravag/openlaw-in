import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'

const SIGN_IN_ENDPOINT = '/api/auth/sign-in'

/**
 * The email sign-in form (Mode B only).
 *
 * A real `<form method="post">` aimed at the real route, so it works with
 * JavaScript off: the route answers a form post with a 303 back to
 * `/signup?sent=1` or `/signup?error=<code>`. The input is what a cheap
 * Android keyboard needs: `type="email"`, `autoComplete`, `inputMode`.
 * Renders nothing in Mode A.
 */
export default function SignInForm({ language }: { language: string }) {
  if (!AUTH_ENABLED) return null
  const strings = getStrings(language)
  return (
    <form method="post" action={SIGN_IN_ENDPOINT} className="flex flex-col gap-3">
      <input type="hidden" name="language" value={language === 'hi' ? 'hi' : 'en'} />
      <label className="flex flex-col gap-1.5 text-base font-semibold text-ink">
        {strings.signInEmailLabel}
        <input
          type="email"
          name="email"
          required
          autoComplete="email"
          inputMode="email"
          placeholder={strings.signInEmailPlaceholder}
          className="min-h-12 w-full rounded-xl border-2 border-paper-edge bg-white px-3 text-base font-normal"
        />
      </label>
      <button
        type="submit"
        className="min-h-12 self-start rounded-xl border-2 border-brand bg-brand px-5 text-base font-semibold text-white"
      >
        {strings.signInSubmit}
      </button>
    </form>
  )
}
