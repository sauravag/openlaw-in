import { AUTH_ENABLED } from '@/lib/authMode'
import { SIGN_IN_PATH } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

interface SessionEndedNoticeProps {
  language: string
}

const LINK_CLASSES = 'font-semibold text-brand underline decoration-2 underline-offset-4'

/**
 * Final review H1a: the conversation this tab was in is kept in the person's
 * account, but the session has lapsed, so the page could not read it. The id
 * is kept; this says why nothing is shown and how to see it again. Mode B only.
 */
export default function SessionEndedNotice({ language }: SessionEndedNoticeProps) {
  if (!AUTH_ENABLED || SIGN_IN_PATH === null) return null
  const strings = getStrings(language)
  const href = language === 'hi' ? `${SIGN_IN_PATH}?language=hi` : SIGN_IN_PATH
  return (
    <p
      data-testid="session-ended"
      role="status"
      className="rounded-xl border border-line bg-tint p-4 text-base text-ink"
    >
      {strings.sessionEndedNotice}{' '}
      <a href={href} className={LINK_CLASSES}>
        {strings.signInCta}
      </a>
    </p>
  )
}
