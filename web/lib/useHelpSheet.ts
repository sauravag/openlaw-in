import { useState } from 'react'

export interface HelpSheet {
  isOpen: boolean
  open: () => void
  close: () => void
}

/** Whether the mobile help sheet is showing. Desktop ignores it: there the
 * side pane is always visible. */
export function useHelpSheet(): HelpSheet {
  const [isOpen, setIsOpen] = useState(false)
  return { isOpen, open: () => setIsOpen(true), close: () => setIsOpen(false) }
}
