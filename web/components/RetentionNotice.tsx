import { ANONYMOUS_UPLOAD_TTL_HOURS, type Retention, SIGN_IN_PATH } from '@/lib/documents'
import { getStrings } from '@/lib/strings'

interface RetentionNoticeProps {
  language: string
  retention: Retention
  /** False while the session is still loading: the notice stays, but its
   * "Sign in" link waits. */
  canOfferSignIn: boolean
}

/**
 * The retention promise for uploaded documents, in whichever form applies.
 *
 * Not buried and not softened: an anonymous upload WILL be deleted, the
 * window is named, and the one action that changes it is offered in the same
 * breath. Mirrors what the server puts in `DocumentSummary.retention_notice`
 * after the upload, so the promise is the same before and after. Renders
 * nothing for `none` (Mode A, nothing expires).
 *
 * Extracted from `DocumentUpload` so the composer can keep it on screen while
 * documents are attached or uploading, not only inside the attach tray.
 */
export default function RetentionNotice({
  language,
  retention,
  canOfferSignIn,
}: RetentionNoticeProps) {
  const strings = getStrings(language)
  if (retention === 'kept') {
    return <p className="text-sm leading-relaxed text-paper-ink">{strings.keptUploadNotice}</p>
  }
  if (retention !== 'expires') return null
  if (SIGN_IN_PATH === null) {
    // Mode A beside a backend that expires uploads (final review H3b): the
    // truth, with no sign-in sentence there is no way to act on.
    return (
      <p className="text-sm leading-relaxed text-paper-ink">
        {strings.expiringUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)}
      </p>
    )
  }
  return (
    <p className="text-sm leading-relaxed text-paper-ink">
      {strings.anonymousUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)}{' '}
      {canOfferSignIn && (
        <a
          href={SIGN_IN_PATH}
          className="font-semibold text-brand underline decoration-2 underline-offset-4"
        >
          {strings.signInCta}
        </a>
      )}
    </p>
  )
}
