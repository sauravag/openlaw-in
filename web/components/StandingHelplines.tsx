import HelplineList from "@/components/HelplineList";
import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
import { getStrings } from "@/lib/strings";

/**
 * The static helpline card, rendered unconditionally (at the top of the side
 * pane, which is also the mobile help sheet) so it is in the server-rendered
 * HTML before any script runs (final whole-plan review, H6): with JavaScript
 * off, every other helpline on the page lives behind state that is never set.
 */
export default function StandingHelplines({ language }: { language: string }) {
  const strings = getStrings(language);
  return (
    <section
      aria-label={strings.helplineHeading}
      className="rounded-2xl border border-brand-soft bg-brand-soft p-4 sm:p-5"
    >
      <h2 className="flex items-center gap-2 text-base font-bold text-brand-ink">
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          className="h-5 w-5 shrink-0"
          fill="currentColor"
        >
          <path d="M6.6 2.3a1.3 1.3 0 0 0-1.7-.1L3.5 3.4C2.7 4.1 2.5 5.3 3 6.4a19.4 19.4 0 0 0 10.6 10.6c1.1.5 2.3.3 3-.5l1.2-1.4a1.3 1.3 0 0 0-.1-1.7l-2.2-2a1.3 1.3 0 0 0-1.7 0l-1 .8a15.6 15.6 0 0 1-5-5l.8-1a1.3 1.3 0 0 0 0-1.7l-2-2.2Z" />
        </svg>
        {strings.helplineHeading}
      </h2>
      <p className="mt-1 text-sm text-muted">{strings.helplineNote}</p>
      <div className="mt-3">
        <HelplineList helplines={getFallbackHelplineSet(language).helplines} />
      </div>
    </section>
  );
}
