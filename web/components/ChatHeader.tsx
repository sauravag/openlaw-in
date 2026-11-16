import { SIDE_PANE_ID } from '@/components/SidePane'
import { AUTH_ENABLED } from '@/lib/authMode'
import { getStrings } from '@/lib/strings'
import { CONVERSATIONS_PANEL_ID } from '@/lib/usePhonePanels'

interface ChatHeaderProps {
  language: string
  isHelpOpen: boolean
  onOpenHelp: () => void
  /** Mode B, signed in: the phone's "Chats" control for the conversation panel. */
  showConversationsButton?: boolean
  isConversationsOpen?: boolean
  onOpenConversations?: () => void
}

interface ChatsButtonProps {
  label: string
  isOpen: boolean
  onOpen?: () => void
}

/** Below `lg` only: on wider screens the sidebar is always a column. */
function ChatsButton({ label, isOpen, onOpen }: ChatsButtonProps) {
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      aria-controls={CONVERSATIONS_PANEL_ID}
      onClick={onOpen}
      className="flex min-h-11 items-center rounded-xl border-2 border-brand px-3 text-base font-semibold text-brand lg:hidden"
    >
      {label}
    </button>
  )
}

/**
 * The top bar: the name, and -- below `lg`, where the side pane is hidden --
 * the "Help" control that opens the full helpline list and the example
 * questions. Every number itself is already on screen in `HelplineStrip`,
 * directly below this bar.
 *
 * "Help" is a link to `#help-pane`, so with JavaScript off the browser's own
 * `:target` shows the sheet; with it on, the click opens the modal sheet.
 * Signed in, a "Chats" button on the left opens the conversation panel.
 */
export default function ChatHeader(props: ChatHeaderProps) {
  const { language, isHelpOpen, onOpenHelp } = props
  const strings = getStrings(language)
  return (
    <header className="shrink-0 border-b border-line bg-white">
      <div className="flex h-14 items-center gap-2 px-4 sm:h-16">
        {/* `AUTH_ENABLED` directly, so the Mode A build drops the button. */}
        {AUTH_ENABLED && props.showConversationsButton && (
          <ChatsButton
            label={strings.conversationsMenuButton}
            isOpen={props.isConversationsOpen === true}
            onOpen={props.onOpenConversations}
          />
        )}
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold tracking-tight text-ink sm:text-2xl">
            OpenLaw<span className="text-brand">.in</span>
          </h1>
          <p className="hidden truncate text-sm text-muted sm:block">{strings.tagline}</p>
        </div>
        <a
          href={`#${SIDE_PANE_ID}`}
          aria-haspopup="dialog"
          aria-expanded={isHelpOpen}
          aria-controls={SIDE_PANE_ID}
          onClick={(event) => {
            event.preventDefault()
            onOpenHelp()
          }}
          className="flex min-h-11 items-center rounded-xl bg-brand px-4 text-base font-semibold text-white no-underline lg:hidden"
        >
          {strings.helpButton}
        </a>
      </div>
    </header>
  )
}
