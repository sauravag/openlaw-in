import { ANONYMOUS_UPLOAD_TTL_HOURS } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

/**
 * Shown when `checkAuthAgreement` proves the frontend build and the backend
 * disagree on the auth mode (final review H3c). Until now only a server log
 * line said so, while the person was the one whose uploads could be deleted
 * after the anonymous window without any notice. Rendered in both modes; it
 * names no sign-in path, so a Mode A build carries no account affordance.
 */
export default function MisconfigurationBanner({ language }: { language: string }) {
  return (
    <p
      role="alert"
      className="rounded-xl border-2 border-paper-edge bg-paper p-4 text-base font-semibold text-paper-ink"
    >
      {getStrings(language).misconfiguredBanner(ANONYMOUS_UPLOAD_TTL_HOURS)}
    </p>
  )
}
