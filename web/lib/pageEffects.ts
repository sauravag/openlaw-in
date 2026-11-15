import { useEffect } from "react";
import { LANGUAGES, type Language } from "@/lib/languages";

function isLanguageCode(value: string | null): value is Language["code"] {
  return LANGUAGES.some((language) => language.code === value);
}

/**
 * Restore `question`/`language` from the URL query string on mount.
 *
 * Task 6 review, M5: the composer's `method="get"` fallback (`components/Composer.tsx`) means a
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

/** `smooth`, unless the reader asked the system for less motion. */
function scrollBehavior(): ScrollBehavior {
  const prefersLessMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return prefersLessMotion ? "auto" : "smooth";
}

/**
 * Scroll `ref`'s element into view whenever `dependency` changes, aligned by
 * `block`. Never on any other render, and never on mount with a null value:
 * someone who has scrolled up to re-read an earlier answer is not yanked
 * down by typing, a language switch, or a session re-check.
 *
 * Task 6 review, H2: requirement 4 is "reachable without scrolling past legal
 * analysis", and DOM order alone does not satisfy that -- a sighted reader
 * gets no signal anything appeared unless the viewport moves. In the chat
 * layout the message list is its own scroll area, and the page scrolls the
 * START of the newest answer turn to the top of it (`block: "start"`), not
 * to the bottom of the list. The crisis block is that turn's first child, so
 * it is the first thing on screen, above the legal analysis.
 *
 * This used to move focus to the result as well, which is how a screen
 * reader heard the answer. That pulled the caret out of the question box and
 * stranded it ~680px above the viewport (the owner's "I have to refresh"
 * report). Focus now stays in the composer; the answer is announced through
 * the polite live region in `MessageList`, and `CrisisAlert` keeps its own
 * `role="alert"`.
 */
export function useScrollIntoViewOnChange(
  ref: React.RefObject<HTMLElement | null>,
  dependency: unknown,
  block: ScrollLogicalPosition = "start",
) {
  useEffect(() => {
    if (dependency === null || dependency === undefined) return;
    ref.current?.scrollIntoView({ behavior: scrollBehavior(), block });
    // `ref` is a stable object; only re-run when the tracked value changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dependency]);
}
