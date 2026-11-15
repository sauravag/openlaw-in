import { AUTH_ENABLED } from '@/lib/authMode'
import { accountDeletedNotice } from '@/lib/accountDeletedStrings'
import { getStrings } from '@/lib/strings'
import type { RedirectNotice as RedirectNoticeKind } from '@/lib/useRedirectNotice'

interface RedirectNoticeProps {
  notice: RedirectNoticeKind | null
  language: string
}

/**
 * The outcome of a sign-in or account-deletion redirect, in a polite live
 * region. The region is in the page from the first render so that a screen
 * reader announces the text when it arrives; it is empty until then. Renders
 * nothing at all in Mode A, where there is no account to report on.
 */
export default function RedirectNotice({ notice, language }: RedirectNoticeProps) {
  if (!AUTH_ENABLED) return null
  return (
    <div data-testid="redirect-notice" role="status" aria-live="polite">
      {notice && (
        <p className="rounded-xl border border-line bg-tint p-4 text-base text-ink">
          {notice.kind === 'migrationFailed'
            ? getStrings(language).authMigrationFailed
            : accountDeletedNotice(language, notice.identityRemoved)}
        </p>
      )}
    </div>
  )
}
