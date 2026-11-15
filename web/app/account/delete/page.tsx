import { notFound, redirect } from 'next/navigation'
import AuthHelplineBlock from '@/components/AuthHelplineBlock'
import { DELETE_CONFIRMATION_WORD } from '@/lib/auth/deleteConfirmation'
import { readSession } from '@/lib/auth/session'
import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'

// Depends on the session cookie and the query string.
export const dynamic = 'force-dynamic'

const DELETE_ENDPOINT = '/api/auth/delete-account'

type SearchParams = Record<string, string | string[] | undefined>

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value
}

/**
 * Delete-account page (Mode B only; a 404 in Mode A). Says what will and will
 * not be erased BEFORE the form, then asks for a typed word: a mis-tap cannot
 * satisfy it. A real form post (no JavaScript needed); the route checks the
 * word server-side and redirects to `/?account=deleted&identity=...`.
 * Signed-out visitors are sent to sign in, since there is nothing to delete.
 */
export default async function DeleteAccountPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  if (!AUTH_ENABLED) notFound()
  const params = await searchParams
  const language = firstValue(params.language) === 'hi' ? 'hi' : 'en'
  if ((await readSession()) === null) {
    redirect(language === 'hi' ? '/signup?language=hi' : '/signup')
  }
  const strings = getStrings(language)
  const word = DELETE_CONFIRMATION_WORD[language]

  return (
    <main lang={language} className="mx-auto w-full max-w-xl px-4 py-8">
      <h1 className="text-2xl font-bold text-brand-ink">{strings.deleteAccountHeading}</h1>
      <ul className="mt-4 flex list-disc flex-col gap-2 pl-5 text-base leading-relaxed text-ink">
        <li>{strings.deleteAccountWillErase}</li>
        <li>{strings.deleteAccountLogKept}</li>
        <li>{strings.deleteAccountIdentityMayRemain}</li>
      </ul>
      <form method="post" action={DELETE_ENDPOINT} className="mt-6 flex flex-col gap-3">
        <input type="hidden" name="language" value={language} />
        <label className="flex flex-col gap-1.5 text-base font-semibold text-ink">
          {strings.deleteAccountInstruction(word)}
          <input
            type="text"
            name="confirmation"
            required
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            className="min-h-12 w-full rounded-xl border-2 border-paper-edge bg-white px-3 text-base font-normal"
          />
        </label>
        <button
          type="submit"
          className="min-h-12 self-start rounded-xl border-2 border-brand bg-brand px-5 text-base font-semibold text-white"
        >
          {strings.deleteAccountSubmit}
        </button>
      </form>
      <p className="mt-6">
        <a href={language === 'hi' ? '/?language=hi' : '/'} className="font-semibold text-brand underline decoration-2 underline-offset-4">
          {strings.deleteAccountCancel}
        </a>
      </p>
      <AuthHelplineBlock language={language} />
    </main>
  )
}
