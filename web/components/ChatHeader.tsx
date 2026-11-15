import { SIDE_PANE_ID } from '@/components/SidePane'
import { getStrings } from '@/lib/strings'

interface ChatHeaderProps {
  language: string
  isHelpOpen: boolean
  onOpenHelp: () => void
}

/**
 * The top bar: the name, and -- below `lg`, where the side pane is hidden --
 * the "Help" control that opens the full helpline list and the example
 * questions. Every number itself is already on screen in `HelplineStrip`,
 * directly below this bar.
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
