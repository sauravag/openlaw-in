import { useRef, type ReactNode } from 'react'
import ExampleQuestions from '@/components/ExampleQuestions'
import StandingHelplines from '@/components/StandingHelplines'
import { getStrings } from '@/lib/strings'
import { useDialogFocus } from '@/lib/useDialogFocus'

export const SIDE_PANE_ID = 'help-pane'

interface SidePaneProps {
  language: string
  /** Mobile only: the pane is showing as a modal sheet. */
  isSheetOpen: boolean
  onCloseSheet: () => void
  onPickExample: (question: string) => void
  /** Mode B extras (the conversation list, the account bar). */
  children?: ReactNode
}

/**
 * Closed on a phone: hidden, unless the no-JS `#help-pane` link made it the
 * `:target`, in which case it shows as the sheet. Desktop: always a column.
 */
const CLOSED_CLASSES =
  'hidden target:fixed target:inset-y-0 target:right-0 target:z-50 target:flex target:shadow-2xl lg:static lg:flex lg:shadow-none'
const OPEN_CLASSES = 'fixed inset-y-0 right-0 z-50 flex shadow-2xl lg:static lg:shadow-none'

function CloseButton({ label, onClose }: { label: string; onClose: () => void }) {
  return (
    // An anchor to "#" so the no-JS `:target` sheet can be closed too.
    <a
      href="#"
      role="button"
      onClick={(event) => {
        event.preventDefault()
        onClose()
      }}
      onKeyDown={(event) => {
        // `role="button"` promises Space works as well as Enter.
        if (event.key !== ' ') return
        event.preventDefault()
        onClose()
      }}
      className="flex min-h-12 items-center gap-2 self-end rounded-xl border border-line bg-white px-4 text-base font-semibold text-ink no-underline lg:hidden"
    >
      <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor">
        <path d="m10 8.6 4.6-4.6L16 5.4 11.4 10l4.6 4.6-1.4 1.4-4.6-4.6L5.4 16 4 14.6 8.6 10 4 5.4 5.4 4 10 8.6Z" />
      </svg>
      {label}
    </a>
  )
}

/**
 * The help column: helplines first, then example questions, then (Mode B)
 * the person's conversations and account.
 *
 * **One node for desktop and mobile.** On `lg` screens it is a fixed-width
 * column beside the chat, always visible and scrolling on its own. Below
 * `lg` it is hidden, and the header's "Help" control opens THIS SAME element
 * as a modal sheet -- not a copy. The static helpline block therefore exists
 * exactly once in the DOM in every state (final whole-plan review, H6: it is
 * in the server HTML before any script runs), and closing the sheet only
 * hides it. While it is closed, `HelplineStrip` keeps every number visible
 * and one tap from a call. With JavaScript off, the "Help" link targets
 * `#help-pane` and CSS `:target` shows the sheet.
 */
export default function SidePane(props: SidePaneProps) {
  const { language, isSheetOpen, onCloseSheet } = props
  const strings = getStrings(language)
  const paneRef = useRef<HTMLElement>(null)
  useDialogFocus(paneRef, isSheetOpen, onCloseSheet)
  const dialogProps = isSheetOpen ? { role: 'dialog', 'aria-modal': true } : {}

  return (
    <aside
      ref={paneRef}
      id={SIDE_PANE_ID}
      aria-label={strings.helpButton}
      {...dialogProps}
      className={`w-[min(22rem,100%)] shrink-0 flex-col gap-6 overflow-y-auto border-l border-line bg-tint p-4 lg:w-80 ${isSheetOpen ? OPEN_CLASSES : CLOSED_CLASSES}`}
    >
      <CloseButton label={strings.closeHelpButton} onClose={onCloseSheet} />
      <StandingHelplines language={language} />
      <ExampleQuestions language={language} onPickExample={props.onPickExample} />
      {props.children}
    </aside>
  )
}
