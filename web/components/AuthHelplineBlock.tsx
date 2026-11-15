import HelplineList from '@/components/HelplineList'
import { getFallbackHelplineSet } from '@/lib/fallbackHelplines'
import { getStrings } from '@/lib/strings'

/**
 * The static helpline block for sign-in and account pages: a person who cannot
 * get in (or is deleting everything) may need the helplines more, not less.
 * Reads only bundled data, so it renders with no backend and no JavaScript.
 */
export default function AuthHelplineBlock({ language }: { language: string }) {
  const strings = getStrings(language)
  const help = getFallbackHelplineSet(language)
  return (
    <section aria-label={strings.helplineHeading} className="mt-8">
      <h2 className="text-lg font-semibold text-brand-ink">{strings.helplineHeading}</h2>
      <p className="mt-1 mb-3 text-sm text-muted">{strings.helplineNote}</p>
      <HelplineList helplines={help.helplines} />
      <p className="mt-3 text-sm text-muted">{help.escalationFooter}</p>
    </section>
  )
}
