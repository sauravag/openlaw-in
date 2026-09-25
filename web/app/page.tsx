"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import AnswerView from "@/components/AnswerView";
import HelplineList from "@/components/HelplineList";
import LanguageSelector from "@/components/LanguageSelector";
import RefusalNotice from "@/components/RefusalNotice";
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
 * signal that anything appeared unless the viewport actually moves.
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
  useScrollIntoViewOnChange(resultRef, result);
  useScrollIntoViewOnChange(errorRef, status === "error" ? errorMessage : null);

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
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-4 py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-slate-900">OpenLaw.in</h1>
        <p className="text-sm text-slate-600">{strings.intro}</p>
      </header>

      {/*
       * Final whole-plan review, H6: with JavaScript disabled (Opera Mini's
       * extreme-saving mode, UC Browser's proxy mode, a stalled bundle on
       * 2G, an ad-blocker breaking hydration) this page never reaches
       * `askQuestion`, and every helpline elsewhere on the page lives behind
       * `result` -- state that never gets set with no JS. This section
       * renders the same static fallback set `askQuestion` itself falls
       * back to on a server failure, unconditionally, so it is present in
       * the server-rendered HTML before any script runs, not merely after a
       * failed request. See `app/__tests__/page.test.tsx`'s coverage of the
       * initial, pre-interaction render.
       */}
      <section
        aria-label={strings.helplineHeading}
        className="rounded-md border border-slate-200 bg-slate-50 p-3"
      >
        <p className="text-sm font-semibold text-slate-700">{strings.helplineHeading}</p>
        <HelplineList helplines={getFallbackHelplineSet(language).helplines} />
      </section>

      <noscript>
        <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          {strings.noScriptNotice}
        </p>
      </noscript>

      <form onSubmit={handleSubmit} method="get" className="flex flex-col gap-3" noValidate>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          {strings.questionLabel}
          <textarea
            name="question"
            className="min-h-24 rounded-md border border-slate-300 px-3 py-2 text-base text-slate-900"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={QUESTION_MAX_LENGTH}
            placeholder={strings.questionPlaceholder}
            required
          />
        </label>

        <LanguageSelector value={language} onChange={setLanguage} name="language" />

        <button
          type="submit"
          disabled={status === "loading" || question.trim().length === 0}
          className="rounded-md bg-slate-900 px-4 py-3 text-base font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === "loading" ? strings.askingButton : strings.askButton}
        </button>
      </form>

      {status === "error" && errorMessage && (
        <p
          ref={errorRef}
          role="alert"
          tabIndex={-1}
          className="rounded-md border border-red-300 bg-red-50 p-3 text-red-800 outline-none"
        >
          {errorMessage}
          {result && <span className="mt-1 block text-sm">{strings.previousAnswerNotice}</span>}
        </p>
      )}

      {result && (
        <div ref={resultRef} tabIndex={-1} className="outline-none">
          {result.kind === "answer" && <AnswerView data={result.data} />}
          {result.kind === "refused" && (
            <RefusalNotice data={result.data} language={language} />
          )}
        </div>
      )}
    </main>
  );
}
