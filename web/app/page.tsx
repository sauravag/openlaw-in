"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import AnswerView from "@/components/AnswerView";
import HelplineList from "@/components/HelplineList";
import LanguageSelector from "@/components/LanguageSelector";
import RefusalNotice from "@/components/RefusalNotice";
import WelcomeGuide from "@/components/WelcomeGuide";
import { askQuestion } from "@/lib/api";
import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
import { DEFAULT_LANGUAGE, LANGUAGES, type Language } from "@/lib/languages";
import { getStrings } from "@/lib/strings";
import type { AnswerData, RefusalData } from "@/lib/types";

type Result =
  | { kind: "answer"; data: AnswerData }
  | { kind: "refused"; data: RefusalData };

type Status = "idle" | "loading" | "error";

const QUESTION_MAX_LENGTH = 2000;

/** One reading measure for the whole page: header, content and footer all
 * line up on it, and no line of legal prose runs wider than the eye can
 * track comfortably on a phone. */
const COLUMN = "mx-auto w-full max-w-xl px-4";

function isLanguageCode(value: string | null): value is Language["code"] {
  return LANGUAGES.some((language) => language.code === value);
}

/**
 * Restore `question`/`language` from the URL query string on mount.
 *
 * Task 6 review, M5: the form's `method="get"` fallback (below) means a
 * submit that happens before React has hydrated -- or with JavaScript off
 * entirely -- performs a real browser GET to `?question=...&language=...`
 * rather than silently discarding what was typed. This is the other half of
 * that fallback: on load, pull those values back out of the URL so the
 * question is not lost even though it was never actually answered. Runs
 * once, client-side only (`window` is unavailable during the server
 * render), and clears the query string afterwards so a page refresh does
 * not keep re-populating the form from a stale URL.
 */
function useRestoreFromQueryString(
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
    window.history.replaceState(null, "", window.location.pathname);
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
function useScrollIntoViewOnChange(
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

/**
 * The chat interface (Task 6).
 *
 * Deliberately a single server-rendered page with one client-side form,
 * not a heavy client build: the primary user is on a cheap Android phone,
 * possibly on a slow connection, and every requirement here is met without
 * a router, global state library, or streaming UI.
 *
 * Requirement 1: the only network call this file makes is `askQuestion`
 * (`lib/api.ts`), which POSTs to `/ask` -- there is no other code path that
 * could call the answer pipeline directly or skip `persist_answer_events`.
 * Requirement 6: no authentication is required anywhere on this page; the
 * form works immediately for an anonymous visitor, and quota is tracked by
 * the cookie `create_app`'s middleware sets on the proxied response.
 *
 * `result` and `status`/`errorMessage` are deliberately separate pieces of
 * state (Task 6 review, M3): a failed follow-up question must not erase an
 * already-rendered crisis box. A new *successful* result replaces the old
 * one; an *error* renders alongside whatever was already on screen.
 *
 * **On the look of it.** White, one column, generous spacing, one accent
 * colour used only where it carries meaning, red reserved entirely for the
 * crisis box. The reader is often frightened and on a bad screen in bad
 * light, so the type is large, the tap targets are at least 48px, and
 * nothing here animates, sells, or asks for anything. See `globals.css`.
 */
export default function HomePage() {
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState<Language["code"]>(DEFAULT_LANGUAGE);
  const [result, setResult] = useState<Result | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const strings = getStrings(language);

  useRestoreFromQueryString(setQuestion, setLanguage);

  // Task 6 review, H3: screen readers must pronounce Hindi content with a
  // Hindi voice, not the `lang="en"` the server-rendered shell starts with.
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const resultRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  useScrollIntoViewOnChange(resultRef, result);
  useScrollIntoViewOnChange(errorRef, status === "error" ? errorMessage : null);

  /** Put an example question in the box and hand the reader the caret --
   * never submit it for them. A mis-tap must not spend a free question. */
  function handlePickExample(example: string) {
    setQuestion(example);
    questionRef.current?.focus();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || status === "loading") {
      return;
    }

    setStatus("loading");
    setErrorMessage(null);
    try {
      // `language` (requirement 2) is always the value the user picked in
      // `LanguageSelector` -- never omitted, never left for the server to
      // detect.
      const outcome = await askQuestion({ question: trimmedQuestion, language });
      setResult(
        outcome.kind === "answer"
          ? { kind: "answer", data: outcome.data }
          : { kind: "refused", data: outcome.data },
      );
      setStatus("idle");
    } catch {
      // `askQuestion` is total (final review, H3) and never throws for a
      // server-side failure -- every one of those resolves to a
      // `{kind: 'refused'}` outcome carrying helplines instead. This catch
      // remains only as defence-in-depth against a genuinely unexpected
      // runtime error in the call itself.
      setStatus("error");
      setErrorMessage(strings.genericError);
      // `result` is deliberately left untouched here -- see the module docstring (M3).
    }
  }

  return (
    <>
      <header className="border-b border-line">
        <div className={`${COLUMN} py-5 sm:py-6`}>
          <h1 className="text-2xl font-bold tracking-tight text-ink">
            OpenLaw<span className="text-brand">.in</span>
          </h1>
          <p className="mt-1 text-base text-muted">{strings.tagline}</p>
        </div>
      </header>

      <main className={`${COLUMN} flex flex-1 flex-col gap-8 py-6 sm:py-8`}>
        {/* What this is, first: a first-time visitor should understand the
         * product before being shown a wall of emergency numbers. Rendered
         * only until there is an answer, so it never sits above a result.
         */}
        {result ? null : (
          <WelcomeGuide language={language} onPickExample={handlePickExample} />
        )}

        {/*
         * Final whole-plan review, H6: with JavaScript disabled (Opera Mini's
         * extreme-saving mode, UC Browser's proxy mode, a stalled bundle on
         * 2G, an ad-blocker breaking hydration) this page never reaches
         * `askQuestion`, and every helpline elsewhere on the page lives behind
         * `result` -- state that never gets set with no JS. This section
         * renders the same static fallback set `askQuestion` itself falls
         * back to on a server failure, unconditionally and ABOVE the form, so
         * it is in the server-rendered HTML before any script runs and before
         * any answer, refusal or legal analysis. The only thing above it is
         * the short "what this is" card (moved there deliberately, so a
         * first-time visitor is not met by a wall of emergency numbers);
         * nothing that requires reading or scrolling past legal content. See
         * `app/__tests__/page.test.tsx`'s coverage of the initial,
         * pre-interaction render.
         */}
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

        <noscript>
          <p className="rounded-xl border border-line bg-tint p-4 text-sm text-ink">
            {strings.noScriptNotice}
          </p>
        </noscript>

        <form onSubmit={handleSubmit} method="get" className="flex flex-col gap-4" noValidate>
          <label className="flex flex-col gap-2">
            <span className="text-base font-semibold text-ink">{strings.questionLabel}</span>
            <textarea
              ref={questionRef}
              name="question"
              className="min-h-32 w-full rounded-xl border border-line bg-white px-4 py-3 text-lg leading-relaxed text-ink placeholder:text-muted"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              maxLength={QUESTION_MAX_LENGTH}
              placeholder={strings.questionPlaceholder}
              required
            />
          </label>

          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <LanguageSelector value={language} onChange={setLanguage} name="language" />

            <button
              type="submit"
              disabled={status === "loading" || question.trim().length === 0}
              className="min-h-14 w-full rounded-xl bg-brand px-6 text-lg font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
            >
              {status === "loading" ? strings.askingButton : strings.askButton}
            </button>
          </div>
        </form>

        {status === "loading" && (
          <p
            role="status"
            className="rounded-xl border border-line bg-tint px-4 py-3 text-base text-muted"
          >
            {strings.thinkingStatus}
          </p>
        )}

        {status === "error" && errorMessage && (
          <p
            ref={errorRef}
            role="alert"
            tabIndex={-1}
            className="rounded-xl border-2 border-crisis-edge bg-crisis-soft p-4 text-base text-crisis outline-none"
          >
            {errorMessage}
            {result && <span className="mt-1 block text-sm">{strings.previousAnswerNotice}</span>}
          </p>
        )}

        {result ? (
          <div ref={resultRef} tabIndex={-1} className="outline-none">
            {result.kind === "answer" && <AnswerView data={result.data} />}
            {result.kind === "refused" && (
              <RefusalNotice data={result.data} language={language} />
            )}
          </div>
        ) : null}
      </main>

      {/* Always on the page, never shouting: the "legal information, not
       * legal advice" line stays visible without dominating anything. */}
      <footer className="border-t border-line">
        <p className={`${COLUMN} py-5 text-sm text-muted`}>{strings.notLegalAdvice}</p>
      </footer>
    </>
  );
}
