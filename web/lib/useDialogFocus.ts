import { useEffect, type RefObject } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE))
}

/** Wrap Tab and Shift+Tab around the ends of `container`. */
function trapTab(event: KeyboardEvent, container: HTMLElement): void {
  const items = focusables(container)
  if (items.length === 0) return
  const first = items[0]
  const last = items[items.length - 1]
  const active = document.activeElement
  const isOutside = !container.contains(active)
  if (event.shiftKey && (active === first || isOutside)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || isOutside)) {
    event.preventDefault()
    first.focus()
  }
}

/**
 * Modal focus handling for a sheet that is not a native `<dialog>`.
 *
 * The help sheet is the same DOM node as the desktop side pane (so the
 * helplines exist exactly once on the page), which rules out `showModal()`.
 * While `isOpen`: focus moves into the container, Tab cannot leave it, and
 * Escape closes it. On close, focus returns to whatever opened it, unless
 * the close itself already moved focus somewhere deliberate.
 */
export function useDialogFocus(
  containerRef: RefObject<HTMLElement | null>,
  isOpen: boolean,
  onClose: () => void,
): void {
  useEffect(() => {
    const container = containerRef.current
    if (!isOpen || container === null) return
    const opener = document.activeElement as HTMLElement | null
    focusables(container)[0]?.focus()

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') onClose()
      else if (event.key === 'Tab' && container) trapTab(event, container)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      // Only hand focus back if it is still in the sheet (or lost): closing
      // because an example was picked has already put the caret in the
      // composer, and that must win.
      const active = document.activeElement
      if (active === null || active === document.body || container.contains(active)) opener?.focus()
    }
    // `onClose` may be a fresh closure each render; re-binding on it would
    // re-run the open-focus step and yank focus to the first control.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen])
}
