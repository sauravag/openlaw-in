import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'

interface AccountBarProps {
  signedIn: boolean
  /** Shown when signed in; ignored otherwise. */
  email: string | null
  language: string
}

const LINK_CLASSES = 'font-semibold text-brand underline decoration-2 underline-offset-4'

/**
 * Sign-in link, or the signed-in email with sign-out and delete-account
 * actions. Renders nothing at all in Mode A: no sign-in affordance, no hint
 * of an account system. Sign-out is a real form post (works without
 * JavaScript); the delete link goes to a page with its own confirmation form.
 */
export default function AccountBar({ signedIn, email, language }: AccountBarProps) {
  if (!AUTH_ENABLED) return null
  const strings = getStrings(language)
  const query = language === 'hi' ? '?language=hi' : ''
  if (!signedIn) {
    return (
      <nav aria-label={strings.accountBarLabel} className="text-sm">
        <a href={`/signup${query}`} className={LINK_CLASSES}>
          {strings.signInCta}
        </a>
      </nav>
    )
  }
  return (
    <nav aria-label={strings.accountBarLabel} className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
      {email && <span className="break-all text-ink">{strings.signedInAs(email)}</span>}
      <form method="post" action="/api/auth/sign-out">
        <button type="submit" className={LINK_CLASSES}>
          {strings.signOutButton}
        </button>
      </form>
      <a href={`/account/delete${query}`} className={LINK_CLASSES}>
        {strings.deleteAccountLink}
      </a>
    </nav>
  )
}
