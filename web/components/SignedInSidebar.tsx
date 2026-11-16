import { useRef } from 'react'
import ConversationSidebar from '@/components/ConversationSidebar'
import { getSidebarStrings } from '@/lib/sidebarStrings'
import { useDialogFocus } from '@/lib/useDialogFocus'
import { CONVERSATIONS_PANEL_ID } from '@/lib/usePhonePanels'
import { useSidebarConversations } from '@/lib/useSidebarConversations'
import { useTitlePreference } from '@/lib/useTitlePreference'

interface SignedInSidebarProps {
  language: string
  email: string | null
  /** Any change reloads the list from page 1, keeping what is shown until it arrives. */
  refreshKey: string
  currentId: string | null
  isBusy: boolean
  /** Mobile only: the sidebar is showing as a modal panel. */
  isPanelOpen: boolean
  onClosePanel: () => void
  onOpen: (conversationId: string) => void
  onNewChat: () => void
}

/** Closed on a phone: hidden. Desktop: always a column left of the chat. */
const CLOSED_CLASSES = 'hidden lg:flex'
const OPEN_CLASSES = 'fixed inset-y-0 left-0 z-50 flex shadow-2xl lg:static lg:shadow-none'

function CloseButton({ label, onClose }: { label: string; onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      className="flex min-h-11 items-center self-end rounded-xl border border-line bg-white px-4 text-base font-semibold text-ink lg:hidden"
    >
      {label}
    </button>
  )
}

/**
 * The signed-in person's conversation sidebar, with its data and the titles
 * preference. Mode B only: the page renders it behind `AUTH_ENABLED` and a
 * resolved, signed-in session, so the Mode A build drops this module and the
 * titled request it leads to (`npm run check:mode-a-bundle`). Unmounting on
 * sign-out drops the list and any titles held in memory.
 *
 * Like `SidePane`, one node serves both layouts: a column on `lg` screens,
 * and below `lg` the same element shown as a modal panel from the left.
 */
export default function SignedInSidebar(props: SignedInSidebarProps) {
  const { language, isPanelOpen, onClosePanel } = props
  const strings = getSidebarStrings(language)
  const { showTitles, setShowTitles } = useTitlePreference(props.email)
  const { conversations, hasMore, loadMore } = useSidebarConversations({
    enabled: true,
    language,
    showTitles,
    refreshKey: props.refreshKey,
  })
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(panelRef, isPanelOpen, onClosePanel)
  const dialogProps = isPanelOpen
    ? { role: 'dialog', 'aria-modal': true, 'aria-label': strings.conversationsNavLabel }
    : {}

  return (
    <div
      ref={panelRef}
      id={CONVERSATIONS_PANEL_ID}
      {...dialogProps}
      className={`w-72 max-w-[85vw] shrink-0 flex-col gap-3 overflow-y-auto border-r border-line bg-white p-3 ${isPanelOpen ? OPEN_CLASSES : CLOSED_CLASSES}`}
    >
      {isPanelOpen && <CloseButton label={strings.closeConversationsPanel} onClose={onClosePanel} />}
      <ConversationSidebar
        language={language}
        conversations={conversations}
        hasMore={hasMore}
        onLoadMore={loadMore}
        currentId={props.currentId}
        showTitles={showTitles}
        onToggleTitles={setShowTitles}
        onOpen={props.onOpen}
        onNewChat={props.onNewChat}
        isBusy={props.isBusy}
      />
    </div>
  )
}
