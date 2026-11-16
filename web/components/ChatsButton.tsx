import { getSidebarStrings } from '@/lib/sidebarStrings'
import { CONVERSATIONS_PANEL_ID } from '@/lib/usePhonePanels'

interface ChatsButtonProps {
  language: string
  isOpen: boolean
  onOpen: () => void
}

/**
 * The phone's "Chats" control, which opens the conversation panel. Below
 * `lg` only: on wider screens the sidebar is always a column. Mode B, signed
 * in only; the page renders it into the header behind `AUTH_ENABLED`, so
 * neither it nor the sidebar copy reaches the Mode A bundle.
 */
export default function ChatsButton({ language, isOpen, onOpen }: ChatsButtonProps) {
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      aria-controls={CONVERSATIONS_PANEL_ID}
      onClick={onOpen}
      className="flex min-h-11 items-center rounded-xl border-2 border-brand px-3 text-base font-semibold text-brand lg:hidden"
    >
      {getSidebarStrings(language).conversationsMenuButton}
    </button>
  )
}
