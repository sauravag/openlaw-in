import { SIDE_PANE_ID } from '@/components/SidePane'
import { getStrings } from '@/lib/strings'

interface ChatHeaderProps {
  language: string
  isHelpOpen: boolean
  onOpenHelp: () => void
}

function PhoneIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="currentColor">
      <path d="M6.6 2.3a1.3 1.3 0 0 0-1.7-.1L3.5 3.4C2.7 4.1 2.5 5.3 3 6.4a19.4 19.4 0 0 0 10.6 10.6c1.1.5 2.3.3 3-.5l1.2-1.4a1.3 1.3 0 0 0-.1-1.7l-2.2-2a1.3 1.3 0 0 0-1.7 0l-1 .8a15.6 15.6 0 0 1-5-5l.8-1a1.3 1.3 0 0 0 0-1.7l-2-2.2Z" />
    </svg>
  )
}

/**
 * The top bar: the name, and -- below `lg`, where the side pane is hidden --
 * a one-tap 112 call link and the "Help" control that opens the full
 * helpline list. Emergency numbers are never more than one tap away.
 *
 * "Help" is a link to `#help-pane`, so with JavaScript off the browser's own
 * `:target` shows the sheet; with it on, the click opens the modal sheet.
 */
export default function ChatHeader({ language, isHelpOpen, onOpenHelp }: ChatHeaderProps) {
  const strings = getStrings(language)
  return (
    <header className="shrink-0 border-b border-line bg-white">
      <div className="flex h-14 items-center gap-2 px-4 sm:h-16">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold tracking-tight text-ink sm:text-2xl">
            OpenLaw<span className="text-brand">.in</span>
          </h1>
          <p className="hidden truncate text-sm text-muted sm:block">{strings.tagline}</p>
        </div>
        <a
          href="tel:112"
          aria-label={strings.emergencyCallLabel}
          className="flex min-h-11 items-center gap-1.5 rounded-xl border-2 border-brand-soft px-3 text-base font-bold text-brand-ink tabular-nums no-underline lg:hidden"
        >
          <PhoneIcon />
          112
        </a>
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
