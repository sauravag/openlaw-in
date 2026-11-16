import { formatConversationTime } from '@/lib/conversationTime'
import { getSidebarStrings } from '@/lib/sidebarStrings'
import type { SidebarConversationData } from '@/lib/types'

interface ConversationSidebarProps {
  language: string
  conversations: readonly SidebarConversationData[]
  hasMore: boolean
  onLoadMore: () => void
  /** The conversation already on the page: marked, and not offered again. */
  currentId: string | null
  showTitles: boolean
  onToggleTitles: (on: boolean) => void
  onOpen: (conversationId: string) => void
  onNewChat: () => void
  /** A turn or an open is in flight: switching conversation now would race it. */
  isBusy: boolean
  /** Pinned in tests so Today/Yesterday are deterministic. */
  now?: Date
}

const ENTRY_CLASS = 'flex min-h-11 w-full flex-col justify-center rounded-lg px-3 py-1.5 text-start'

interface EntryProps {
  item: SidebarConversationData
  language: string
  showTitles: boolean
  now: Date
}

/** The two lines of an entry. The title line exists only when titles are on. */
function EntryText({ item, language, showTitles, now }: EntryProps) {
  const strings = getSidebarStrings(language)
  const documents = strings.conversationDocuments(item.document_count)
  const time = formatConversationTime(item.updated_at, language, now)
  return (
    <>
      {showTitles && (
        <span className="truncate text-sm font-semibold text-ink">
          {item.title || strings.documentsOnlyTitle}
        </span>
      )}
      <span className="truncate text-xs text-muted">
        {documents ? `${time} · ${documents}` : time}
      </span>
    </>
  )
}

/**
 * The signed-in owner's conversations, newest first. Presentational: the page
 * owns the data, the open conversation and the titles preference.
 *
 * An entry shows when it happened and how many documents it holds; its title
 * (a few words from the first question) appears only when the owner has
 * switched titles on, so nothing personal is on screen by default.
 */
export default function ConversationSidebar({
  language,
  conversations,
  hasMore,
  onLoadMore,
  currentId,
  showTitles,
  onToggleTitles,
  onOpen,
  onNewChat,
  isBusy,
  now = new Date(),
}: ConversationSidebarProps) {
  const strings = getSidebarStrings(language)
  return (
    <nav aria-label={strings.conversationsNavLabel} className="flex h-full flex-col gap-3">
      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={onNewChat}
          disabled={isBusy}
          className="min-h-11 rounded-lg border-2 border-brand px-4 text-sm font-semibold text-brand disabled:opacity-50"
        >
          {strings.newChatButton}
        </button>
        <label className="flex min-h-11 items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={showTitles}
            onChange={(event) => onToggleTitles(event.target.checked)}
            className="h-5 w-5"
          />
          {strings.showTitlesLabel}
        </label>
      </div>
      <ol className="flex flex-1 flex-col gap-1 overflow-y-auto">
        {conversations.map((item) => (
          <li key={item.id}>
            {item.id === currentId ? (
              <div aria-current="page" className={`${ENTRY_CLASS} bg-brand-soft`}>
                <EntryText item={item} language={language} showTitles={showTitles} now={now} />
              </div>
            ) : (
              <button
                type="button"
                onClick={() => onOpen(item.id)}
                disabled={isBusy}
                className={`${ENTRY_CLASS} border border-line disabled:opacity-50`}
              >
                <EntryText item={item} language={language} showTitles={showTitles} now={now} />
              </button>
            )}
          </li>
        ))}
        {hasMore && (
          <li>
            <button
              type="button"
              onClick={onLoadMore}
              className="min-h-11 w-full rounded-lg text-sm font-semibold text-brand"
            >
              {strings.showMoreConversations}
            </button>
          </li>
        )}
      </ol>
    </nav>
  )
}
