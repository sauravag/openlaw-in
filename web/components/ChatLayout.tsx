import type { ReactNode } from 'react'

interface ChatLayoutProps {
  header: ReactNode
  messages: ReactNode
  composer: ReactNode
  sidePane: ReactNode
  /** Mobile only: dims the chat behind the open help sheet; a tap closes it. */
  isSheetOpen: boolean
  onCloseSheet: () => void
}

/**
 * The frame of the chat page.
 *
 * Full dynamic-viewport height (`h-dvh`, which tracks mobile browsers'
 * collapsing toolbars) with nothing scrolling at the page level: the header
 * stays at the top, the composer is pinned at the bottom, and only the
 * message list between them scrolls. On `lg` screens the side pane sits to
 * the right as its own independently scrolling column.
 */
export default function ChatLayout(props: ChatLayoutProps) {
  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-white">
      {props.header}
      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          {props.messages}
          <div className="shrink-0 border-t border-line bg-white px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-4">
            {props.composer}
          </div>
        </main>
        {props.sidePane}
      </div>
      {props.isSheetOpen && (
        <div
          aria-hidden="true"
          onClick={props.onCloseSheet}
          className="fixed inset-0 z-40 bg-ink/40 lg:hidden"
        />
      )}
    </div>
  )
}
