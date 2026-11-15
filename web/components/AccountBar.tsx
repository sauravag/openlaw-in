import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'
import { forgetStoredConversationId } from '@/lib/thread'

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
 * JavaScript; with it, submitting also forgets the stored conversation
 * pointer); the delete link goes to a page with its own confirmation form.
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
      {/* N1: a deliberate sign-out forgets the kept conversation pointer (and
        * its signed-in flag) before the browser posts. Otherwise the same tab,
        * now anonymous, would read it as a LAPSED session and show "your
        * session has ended, sign in" -- on a shared phone, to the next person,
        * with no way to dismiss it. A session that lapses on its own never
        * passes through here, so it still gets that notice. */}
      <form method="post" action="/api/auth/sign-out" onSubmit={forgetStoredConversationId}>
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
