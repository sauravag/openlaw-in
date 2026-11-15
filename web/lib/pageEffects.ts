import { useEffect } from "react";
import { LANGUAGES, type Language } from "@/lib/languages";

function isLanguageCode(value: string | null): value is Language["code"] {
  return LANGUAGES.some((language) => language.code === value);
}

/**
 * Restore `question`/`language` from the URL query string on mount.
 *
 * Task 6 review, M5: the form's `method="get"` fallback (in `app/page.tsx`) means a
 * submit that happens before React has hydrated -- or with JavaScript off
 * entirely -- performs a real browser GET to `?question=...&language=...`
 * rather than silently discarding what was typed. This is the other half of
 * that fallback: on load, pull those values back out of the URL so the
 * question is not lost even though it was never actually answered. Runs
 * once, client-side only (`window` is unavailable during the server
 * render), and clears the query string afterwards so a page refresh does
 * not keep re-populating the form from a stale URL.
 */
export function useRestoreFromQueryString(
  setQuestion: (value: string) => void,
  setLanguage: (value: Language["code"]) => void,
) {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const restoredQuestion = params.get("question");
    const restoredLanguage = params.get("language");
    if (!restoredQuestion && !restoredLanguage) return;

    if (restoredQuestion) setQuestion(restoredQuestion);
    if (isLanguageCode(restoredLanguage)) setLanguage(restoredLanguage);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
    // Runs once on mount only -- setQuestion/setLanguage are stable setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/**
 * Scroll and move focus to `ref`'s element whenever `dependency` changes.
 *
 * Task 6 review, H2: requirement 4 is "reachable without scrolling past
 * legal analysis," and DOM order alone does not satisfy that on a phone --
 * the form itself pushes the result ~440-520px down. A sighted user gets no
 * signal that anything appeared unless the viewport actually moves. The
 * element focused is the result wrapper, whose FIRST child is the crisis
 * box whenever there is one, so focus lands above the legal content rather
 * than after it.
 */
export function useScrollIntoViewOnChange(
  ref: React.RefObject<HTMLElement | null>,
  dependency: unknown,
) {
  useEffect(() => {
    if (dependency === null || dependency === undefined) return;
    ref.current?.focus();
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    // `ref` is a stable object; only re-run when the tracked value changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dependency]);
}
