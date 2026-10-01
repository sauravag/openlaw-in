"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import ConversationThread from "@/components/ConversationThread";
import CrisisAlert from "@/components/CrisisAlert";
import DocumentList from "@/components/DocumentList";
import DocumentUpload from "@/components/DocumentUpload";
import HelplineList from "@/components/HelplineList";
import LanguageSelector from "@/components/LanguageSelector";
import RefusalNotice from "@/components/RefusalNotice";
import WelcomeGuide from "@/components/WelcomeGuide";
import { askQuestion, readConversation, deleteConversation } from "@/lib/api";
import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
import { DEFAULT_LANGUAGE, LANGUAGES, type Language } from "@/lib/languages";
import { getStrings } from "@/lib/strings";
import {
  appendTurn,
  forgetStoredConversationId,
  latestTurn,
  readStoredConversationId,
  replayedTurn,
  storeConversationId,
  turnCrisis,
  type ThreadTurn,
  type TurnOutcome,
} from "@/lib/thread";
import { useDocuments } from "@/lib/useDocuments";

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
 * Bring back the conversation this browser tab was in the middle of.
 *
 * The id lives in `sessionStorage` (see `lib/thread.ts` for why not
 * `localStorage`), and the turns themselves come from
 * `GET /conversations/{id}`, never from storage -- a transcript cached in the
 * browser could outlive the deletion of the real one, and the authoritative
 * answer to "does this still exist, and is it yours?" belongs to the server.
 *
 * A refusal is the ORDINARY case, not an error: an anonymous conversation
 * expires after `Settings.anonymous_upload_ttl_hours`, and the cookie that
 * identified its owner can be cleared at any time. The stored id is forgotten
 * and the page simply starts fresh; nothing is put in front of the reader,
 * who did nothing wrong.
 */
function useRestoreConversation(
  language: Language["code"],
  onRestored: (conversationId: string, turns: ThreadTurn[]) => void,
) {
  useEffect(() => {
    const storedId = readStoredConversationId();
    if (storedId === null) return;

    let isStale = false;
    void readConversation({ conversationId: storedId, language }).then((outcome) => {
      if (isStale) return;
      if (outcome.kind !== "conversation") {
        forgetStoredConversationId();
        return;
      }
      onRestored(outcome.data.conversation_id, outcome.data.turns.map(replayedTurn));
    });
    return () => {
      isStale = true;
    };
    // Runs once on mount: a language change must not re-fetch the thread, and
    // `onRestored` is a stable closure over setState calls.
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
 * The conversation interface (Task 6, extended to a thread by Plan 6 Task 17).
 *
 * Deliberately a single server-rendered page with one client-side form,
 * not a heavy client build: the primary user is on a cheap Android phone,
 * possibly on a slow connection, and every requirement here is met without
 * a router, global state library, or streaming UI.
 *
 * Requirement 1: every question this page asks goes through `askQuestion`
 * (`lib/api.ts`), which POSTs to `/ask` -- there is no other code path that
 * could call the answer pipeline directly or skip `persist_answer_events`.
 * Requirement 6: no authentication is required anywhere on this page; the
 * form works immediately for an anonymous visitor, and quota is tracked by
 * the cookie `create_app`'s middleware sets on the proxied response.
 *
 * **Crisis content is hoisted above the whole thread.** A thread renders
 * oldest turn first, so the newest answer -- the only one that can carry a
 * fresh crisis response -- sits at the BOTTOM, below every earlier turn's
 * legal analysis. That is exactly the failure requirement 4 forbids, and it
 * is a failure a thread introduces that a single-answer page did not have.
 * So the newest turn's crisis block is rendered here, first, above the list,
 * and `ConversationThread` is told to omit that turn's own copy
 * (`suppressCrisisForTurnId`). Every older turn keeps its box where it is:
 * help once delivered is never withdrawn (Task 6 review, M3). Focus lands on
 * the wrapper whose first child is that hoisted box.
 *
 * `turns` and `status`/`errorMessage` are deliberately separate pieces of
 * state (Task 6 review, M3): a failed follow-up question must not erase an
 * already-rendered crisis box. A new turn is appended; an *error* renders
 * alongside everything that was already on screen.
 *
 * **On the look of it.** White, one column, generous spacing, one accent
 * colour used only where it carries meaning, red reserved entirely for the
 * crisis box, and a warm paper tone reserved entirely for the reader's own
 * uploaded documents. The reader is often frightened and on a bad screen in
 * bad light, so the type is large, the tap targets are at least 48px, and
 * nothing here animates, sells, or asks for anything. See `globals.css`.
 */
export default function HomePage() {
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState<Language["code"]>(DEFAULT_LANGUAGE);
  const [turns, setTurns] = useState<readonly ThreadTurn[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const strings = getStrings(language);
  const turnCounter = useRef(0);

  /** Remember the conversation the server attached this turn or upload to. */
  function rememberConversation(id: string) {
    setConversationId(id);
    storeConversationId(id);
  }

  const attachments = useDocuments({
    language,
    conversationId,
    onConversationStarted: rememberConversation,
  });

  useRestoreFromQueryString(setQuestion, setLanguage);
  useRestoreConversation(language, (restoredId, restoredTurns) => {
    setConversationId(restoredId);
    setTurns(restoredTurns);
  });

  // Task 6 review, H3: screen readers must pronounce Hindi content with a
  // Hindi voice, not the `lang="en"` the server-rendered shell starts with.
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const threadRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const uploadRefusalRef = useRef<HTMLDivElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const newestTurn = latestTurn(turns);
  const hoistedCrisis = newestTurn === null ? null : turnCrisis(newestTurn);
  useScrollIntoViewOnChange(threadRef, newestTurn?.id ?? null);
  useScrollIntoViewOnChange(errorRef, status === "error" ? errorMessage : null);
  useScrollIntoViewOnChange(uploadRefusalRef, attachments.refusal);

  /** Put an example question in the box and hand the reader the caret --
   * never submit it for them. A mis-tap must not spend a free question. */
  function handlePickExample(example: string) {
    setQuestion(example);
    questionRef.current?.focus();
  }

  /** Append one finished turn, recording the documents it was asked with. */
  function recordTurn(askedQuestion: string, outcome: TurnOutcome) {
    turnCounter.current += 1;
    setTurns((current) =>
      appendTurn(current, {
        id: `turn-${turnCounter.current}`,
        question: askedQuestion,
        language,
        // Captured now, not read at render time: deleting a document later
        // must not rewrite what an earlier turn says was read for it.
        attachedFilenames: attachments.documents.map((document) => document.filename),
        outcome,
      }),
    );
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
      // detect. `documentIds` names only documents already uploaded to this
      // conversation; no bytes travel through `/ask`.
      const outcome = await askQuestion({
        question: trimmedQuestion,
        language,
        conversationId,
        documentIds: attachments.documents.map((document) => document.id),
      });
      if (outcome.kind === "answer" && outcome.data.conversation_id) {
        rememberConversation(outcome.data.conversation_id);
      }
      recordTurn(trimmedQuestion, outcome);
      setQuestion("");
      setStatus("idle");
    } catch {
      // `askQuestion` is total (final review, H3) and never throws for a
      // server-side failure -- every one of those resolves to a
      // `{kind: 'refused'}` outcome carrying helplines instead. This catch
      // remains only as defence-in-depth against a genuinely unexpected
      // runtime error in the call itself.
      setStatus("error");
      setErrorMessage(strings.genericError);
      // `turns` is deliberately left untouched here -- see the module docstring (M3).
    }
  }

  /**
   * Delete the conversation, its turns and every document in it.
   *
   * The broadest withdrawal this interface offers, and the only one that
   * clears uploaded documents in bulk. The local state is cleared only after
   * the server confirms: a thread that vanished from the screen while the
   * files were still stored would be the worst possible lie to tell here.
   */
  async function handleDeleteConversation() {
    if (conversationId === null) return;
    const outcome = await deleteConversation({ conversationId, language });
    if (outcome.kind === "refused") {
      setStatus("error");
      setErrorMessage(outcome.data.detail);
      return;
    }
    setTurns([]);
    setConversationId(null);
    forgetStoredConversationId();
    attachments.clear();
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
         * only until the conversation has started, so it never sits above a
         * result.
         */}
        {turns.length > 0 ? null : (
          <WelcomeGuide language={language} onPickExample={handlePickExample} />
        )}

        {/*
         * Final whole-plan review, H6: with JavaScript disabled (Opera Mini's
         * extreme-saving mode, UC Browser's proxy mode, a stalled bundle on
         * 2G, an ad-blocker breaking hydration) this page never reaches
         * `askQuestion`, and every helpline elsewhere on the page lives behind
         * state that never gets set with no JS. This section renders the same
         * static fallback set `askQuestion` itself falls back to on a server
         * failure, unconditionally and ABOVE the form, so it is in the
         * server-rendered HTML before any script runs and before any answer,
         * refusal or legal analysis. The only thing above it is the short
         * "what this is" card (moved there deliberately, so a first-time
         * visitor is not met by a wall of emergency numbers); nothing that
         * requires reading or scrolling past legal content. See
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

        {/* The reader's own paperwork, in its own clearly-marked section --
          * below the question it will be read for, above the answers it will
          * appear in.
          *
          * **Known gap, recorded rather than papered over:**
          * `GET /conversations/{id}` returns turns only, with no document
          * list, so after a page reload this section starts empty even for a
          * conversation that still holds files. The per-document delete
          * control therefore cannot be offered for a restored conversation;
          * "delete this conversation and the documents in it" (below) still
          * can, and cascades server-side, so a person is never left unable to
          * withdraw a file -- only unable to withdraw one of several
          * individually. Closing it properly needs a documents field on
          * `ConversationView`.
          *
          * The upload control is a real multipart form, so a no-JavaScript
          * visitor's file is stored rather than silently lost -- see
          * `DocumentUpload`.
          */}
        <section
          aria-label={strings.documentsHeading}
          className="flex flex-col gap-4 rounded-2xl border border-line bg-tint p-4 sm:p-5"
        >
          <h2 className="text-base font-bold text-ink">{strings.documentsHeading}</h2>
          <DocumentList
            documents={attachments.documents}
            language={language}
            onDelete={(documentId) => void attachments.removeDocument(documentId)}
            deletingId={attachments.deletingId}
          />
          <DocumentUpload
            language={language}
            conversationId={conversationId}
            // No browser-side session exists in this build, so every visitor
            // is anonymous and must be told their upload expires. The prop is
            // here so the notice cannot be forgotten when sign-in lands.
            isSignedIn={false}
            isUploading={attachments.uploadingFilename !== null}
            uploadingFilename={attachments.uploadingFilename}
            documentCount={attachments.documents.length}
            onFileChosen={(file) => void attachments.attachFile(file)}
          />
        </section>

        {attachments.refusal && (
          <div ref={uploadRefusalRef} tabIndex={-1} className="outline-none">
            {/* Every upload and delete failure -- too large, unsupported type,
              * extraction failed, too many documents, uploads switched off,
              * and a delete the server refused -- renders through the same
              * surface as every other refusal, so it arrives with the
              * escalation footer and the tap-to-call helplines attached
              * (requirement 6). */}
            <RefusalNotice data={attachments.refusal} language={language} />
          </div>
        )}

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
            {turns.length > 0 && (
              <span className="mt-1 block text-sm">{strings.previousAnswerNotice}</span>
            )}
          </p>
        )}

        {turns.length > 0 && (
          <div ref={threadRef} tabIndex={-1} className="flex flex-col gap-8 outline-none">
            {/* FIRST, above the thread: see the module docstring. */}
            {hoistedCrisis && (
              <CrisisAlert
                crisisResponse={hoistedCrisis.crisisResponse}
                helplines={hoistedCrisis.helplines}
                language={hoistedCrisis.language}
              />
            )}
            <ConversationThread
              turns={turns}
              language={language}
              suppressCrisisForTurnId={hoistedCrisis === null ? null : newestTurn?.id ?? null}
            />
          </div>
        )}

        {conversationId !== null && (
          <button
            type="button"
            onClick={() => void handleDeleteConversation()}
            className="min-h-12 self-start rounded-xl border border-line px-4 text-sm font-semibold text-muted"
          >
            {strings.deleteConversationLabel}
          </button>
        )}
      </main>

      {/* Always on the page, never shouting: the "legal information, not
       * legal advice" line stays visible without dominating anything. */}
      <footer className="border-t border-line">
        <p className={`${COLUMN} py-5 text-sm text-muted`}>{strings.notLegalAdvice}</p>
      </footer>
    </>
  );
}
