import { useState } from 'react'

/** One phone panel: whether it is showing, and how to show or hide it. */
export interface HelpSheet {
  isOpen: boolean
  open: () => void
  close: () => void
}

/** The id of the signed-in conversations panel, for the header's `aria-controls`. */
export const CONVERSATIONS_PANEL_ID = 'conversations-panel'

type PanelName = 'help' | 'conversations'

/**
 * Which phone panel is showing: the help sheet, the conversations panel
 * (Mode B, signed in), or neither. Opening one closes the other, so there is
 * only ever one backdrop. Desktop ignores both: there each is a column.
 *
 * `hasConversations` is whether the sidebar exists (Mode B, signed in). When
 * it goes away the conversations panel is closed, so it does not pop open
 * again on the next sign-in.
 */
export function usePhonePanels(hasConversations: boolean): {
  help: HelpSheet
  conversations: HelpSheet
} {
  const [openPanel, setOpenPanel] = useState<PanelName | null>(null)
  // Adjusted during render, React's sanctioned way to react to changed inputs.
  if (!hasConversations && openPanel === 'conversations') setOpenPanel(null)

  function panel(name: PanelName): HelpSheet {
    return {
      isOpen: openPanel === name,
      open: () => setOpenPanel(name),
      // Closing one panel never closes the other.
      close: () => setOpenPanel((current) => (current === name ? null : current)),
    }
  }

  return { help: panel('help'), conversations: panel('conversations') }
}
