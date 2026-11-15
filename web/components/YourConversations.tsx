import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'
import type { ConversationListItemData } from '@/lib/types'

interface YourConversationsProps {
  conversations: readonly ConversationListItemData[]
  /** The conversation already on the page, which is marked rather than offered. */
  currentId: string | null
  language: string
  onOpen: (conversationId: string) => void
}

/** A start date the reader can recognise, in their own language. */
function startedOn(isoTimestamp: string, language: string): string {
  const date = new Date(isoTimestamp)
  if (Number.isNaN(date.getTime())) return isoTimestamp
  return date.toLocaleDateString(language === 'hi' ? 'hi-IN' : 'en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/**
 * "Your conversations" (final review H1b): the signed-in owner's own list,
 * from `GET /conversations`. It is what makes "kept until you delete it" a
 * promise a person can actually use: a new phone, a closed tab or a sign-in
 * after the session lapsed all start with no remembered id, and without this
 * list a kept conversation would sit in storage with no way back to it.
 *
 * Metadata only (a date and a document count), so nothing personal is shown
 * before the person chooses to open one. Mode B only.
 */
export default function YourConversations({
  conversations,
  currentId,
  language,
  onOpen,
}: YourConversationsProps) {
  if (!AUTH_ENABLED || conversations.length === 0) return null
  const strings = getStrings(language)
  return (
    <section aria-labelledby="your-conversations" className="flex flex-col gap-3">
      <h2 id="your-conversations" className="text-base font-bold text-ink">
        {strings.yourConversationsHeading}
      </h2>
      <ul aria-labelledby="your-conversations" className="flex flex-col gap-2">
        {conversations.map((item) => (
          <li
            key={item.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-line p-3"
          >
            <span className="text-sm text-ink">
              {strings.conversationListItem(startedOn(item.created_at, language), item.document_count)}
            </span>
            {item.id === currentId ? (
              <span className="text-sm font-semibold text-muted">{strings.currentConversationLabel}</span>
            ) : (
              <button
                type="button"
                onClick={() => onOpen(item.id)}
                className="min-h-12 min-w-12 rounded-lg border-2 border-brand px-4 text-sm font-semibold text-brand"
              >
                {strings.openConversationButton}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
