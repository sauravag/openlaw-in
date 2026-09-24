"use client";

import { type FormEvent, useState } from "react";
import AnswerView from "@/components/AnswerView";
import LanguageSelector from "@/components/LanguageSelector";
import RefusalNotice from "@/components/RefusalNotice";
import { AskError, askQuestion } from "@/lib/api";
import { DEFAULT_LANGUAGE, type Language } from "@/lib/languages";
import type { AnswerData, RefusalData } from "@/lib/types";

type ViewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "answer"; data: AnswerData }
  | { kind: "refused"; data: RefusalData }
  | { kind: "error"; message: string };

const QUESTION_MAX_LENGTH = 2000;

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
 */
export default function HomePage() {
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState<Language["code"]>(DEFAULT_LANGUAGE);
  const [view, setView] = useState<ViewState>({ kind: "idle" });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || view.kind === "loading") {
      return;
    }

    setView({ kind: "loading" });
    try {
      // `language` (requirement 2) is always the value the user picked in
      // `LanguageSelector` -- never omitted, never left for the server to
      // detect.
      const outcome = await askQuestion({ question: trimmedQuestion, language });
      setView(
        outcome.kind === "answer"
          ? { kind: "answer", data: outcome.data }
          : { kind: "refused", data: outcome.data },
      );
    } catch (error: unknown) {
      setView({
        kind: "error",
        message:
          error instanceof AskError
            ? error.message
            : "Something went wrong. Please try again.",
      });
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-4 py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-slate-900">OpenLaw.in</h1>
        <p className="text-sm text-slate-600">
          Ask a question about Indian law and get an answer grounded in a
          government source you can check yourself. This is legal
          information, not legal advice. No account needed.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="flex flex-col gap-3" noValidate>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Your question / आपका सवाल
          <textarea
            className="min-h-24 rounded-md border border-slate-300 px-3 py-2 text-base text-slate-900"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={QUESTION_MAX_LENGTH}
            placeholder="e.g. How much notice must my landlord give before eviction?"
            required
          />
        </label>

        <LanguageSelector value={language} onChange={setLanguage} />

        <button
          type="submit"
          disabled={view.kind === "loading" || question.trim().length === 0}
          className="rounded-md bg-slate-900 px-4 py-3 text-base font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {view.kind === "loading" ? "Asking…" : "Ask"}
        </button>
      </form>

      {view.kind === "error" && (
        <p
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 p-3 text-red-800"
        >
          {view.message}
        </p>
      )}

      {view.kind === "answer" && <AnswerView data={view.data} />}
      {view.kind === "refused" && <RefusalNotice data={view.data} />}
    </main>
  );
}
