import { getFallbackHelplineSet } from '@/lib/fallbackHelplines'
import { getStrings } from '@/lib/strings'

interface HelplineStripProps {
  language: string
}

/**
 * Every helpline number, always visible on a phone, one tap from a call.
 *
 * Below `lg` the side pane (with the full labelled list) is a sheet behind a
 * "Help" tap. H6 asks for the numbers to be perceivable before any
 * interaction, so this strip sits under the header, outside the scrolling
 * thread, on every phone screen. It is never `display:none` or `aria-hidden`
 * there; `lg:hidden` applies only where the side pane is a visible column.
 *
 * Same data as `StandingHelplines` and `HelplineList`
 * (`getFallbackHelplineSet`), so there is no third copy of the numbers to
 * drift. Each chip's accessible name is `Label: number`, label first, the
 * same convention as `HelplineList`; the label is visually truncated, never
 * dropped. On a narrow phone the six chips sit in a 3x2 grid so every number
 * is on screen at once with no swipe; from `sm` up they fit (or scroll) on
 * one line. Server-rendered with no hooks, so it works
 * with JavaScript off.
 */
export default function HelplineStrip({ language }: HelplineStripProps) {
  const strings = getStrings(language)
  const { helplines } = getFallbackHelplineSet(language)
  return (
    <section
      aria-label={strings.helplineStripLabel}
      className="shrink-0 border-b border-line bg-brand-soft lg:hidden"
    >
      <ul className="grid grid-cols-3 gap-1.5 px-3 py-2 sm:flex sm:overflow-x-auto">
        {helplines.map((helpline) => (
          <li key={helpline.number} className="min-w-0 sm:shrink-0">
            <a
              href={`tel:${helpline.number}`}
              className="flex min-h-11 flex-col justify-center rounded-lg border border-brand-soft bg-white px-2.5 py-1 leading-tight text-brand-ink no-underline sm:max-w-[11rem]"
            >
              <span className="truncate text-xs font-medium">{helpline.label}: </span>
              <span className="text-base font-bold tabular-nums">{helpline.number}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}
