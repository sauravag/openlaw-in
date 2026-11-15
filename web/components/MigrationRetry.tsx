import { useState } from 'react'
import RefusalNotice from '@/components/RefusalNotice'
import { retryMigration } from '@/lib/api'
import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'
import type { RefusalData } from '@/lib/types'

interface MigrationRetryProps {
  language: string
  /** Called once the earlier conversations are in the account. */
  onMigrated: () => void
}

type RetryState =
  | { kind: 'idle' }
  | { kind: 'retrying' }
  | { kind: 'migrated' }
  | { kind: 'refused'; data: RefusalData }

const BUTTON_CLASSES =
  'mt-3 min-h-12 rounded-xl border-2 border-brand px-5 text-base font-semibold text-brand disabled:opacity-50'

/**
 * `?migration=failed` (final review H2). Signed in, every request now carries
 * the account's token, so the earlier anonymous material is NOT visible in
 * this browser: the copy says so, says what happens if nothing is done, and
 * offers a retry that reports its outcome. A failed retry renders through
 * `RefusalNotice`, so it carries the footer and tap-to-call helplines.
 */
export default function MigrationRetry({ language, onMigrated }: MigrationRetryProps) {
  const [state, setState] = useState<RetryState>({ kind: 'idle' })
  if (!AUTH_ENABLED) return null
  const strings = getStrings(language)

  async function handleRetry() {
    setState({ kind: 'retrying' })
    const outcome = await retryMigration({ language })
    if (outcome.kind === 'migrated') {
      setState({ kind: 'migrated' })
      onMigrated()
      return
    }
    setState({ kind: 'refused', data: outcome.data })
  }

  if (state.kind === 'migrated') {
    return <p className="rounded-xl border border-line bg-tint p-4 text-base text-ink">{strings.migrationSucceeded}</p>
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl border border-line bg-tint p-4 text-base text-ink">
        <p>{strings.authMigrationFailed}</p>
        <button
          type="button"
          onClick={() => void handleRetry()}
          disabled={state.kind === 'retrying'}
          className={BUTTON_CLASSES}
        >
          {state.kind === 'retrying' ? strings.migrationRetrying : strings.migrationRetryButton}
        </button>
      </div>
      {state.kind === 'refused' && <RefusalNotice data={state.data} language={language} />}
    </div>
  )
}
