"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import AccountBar from "@/components/AccountBar";
import ConversationThread from "@/components/ConversationThread";
import CrisisAlert from "@/components/CrisisAlert";
import DocumentList from "@/components/DocumentList";
import DocumentUpload from "@/components/DocumentUpload";
import LanguageSelector from "@/components/LanguageSelector";
import RedirectNotice from "@/components/RedirectNotice";
import RefusalNotice from "@/components/RefusalNotice";
import SessionEndedNotice from "@/components/SessionEndedNotice";
import StandingHelplines from "@/components/StandingHelplines";
import WelcomeGuide from "@/components/WelcomeGuide";
import YourConversations from "@/components/YourConversations";
import { AUTH_ENABLED } from "@/lib/authMode";
import { askQuestion, readConversation, deleteConversation } from "@/lib/api";
import { DEFAULT_LANGUAGE, type Language } from "@/lib/languages";
import { useRestoreFromQueryString, useScrollIntoViewOnChange } from "@/lib/pageEffects";
import { getStrings } from "@/lib/strings";
import {
  appendTurn,
  forgetStoredConversationId,
  latestTurn,
  replayedTurn,
  storeConversationId,
  turnCrisis,
  type ThreadTurn,
  type TurnOutcome,
} from "@/lib/thread";
import {
  attachedFromConversationCard,
  type Retention,
  type AttachedDocument,
} from "@/lib/documents";
import { useConversationList } from "@/lib/useConversationList";
import { useDocuments } from "@/lib/useDocuments";
import { useRedirectNotice } from "@/lib/useRedirectNotice";
import { useRestoreConversation } from "@/lib/useRestoreConversation";
import { useSession } from "@/lib/useSession";

type Status = "idle" | "loading" | "error";

const QUESTION_MAX_LENGTH = 2000;

/** One reading measure for the whole page: header, content and footer all
 * line up on it, and no line of legal prose runs wider than the eye can
 * track comfortably on a phone. */
const COLUMN = "mx-auto w-full max-w-xl px-4";

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
  const [isSessionEnded, setIsSessionEnded] = useState(false);
  const strings = getStrings(language);
  const turnCounter = useRef(0);
  // Must run before `useRestoreFromQueryString`, which may rewrite the URL.
  const redirectNotice = useRedirectNotice();
  const session = useSession();

  /** Remember the conversation the server attached this turn or upload to,
   * and whether it was stored while signed in (so a later lapse keeps it). */
  function rememberConversation(id: string) {
    setConversationId(id);
    storeConversationId(id, AUTH_ENABLED && session.signedIn);
  }

  const attachments = useDocuments({
    language,
    conversationId,
    onConversationStarted: rememberConversation,
  });

  // Referenced as `AUTH_ENABLED` directly so Mode A's build folds this to
  // "none" and drops every account branch.
  // Ground truth wins: a document that carries an expiry never sits under a
  // "kept" banner, whatever the (possibly stale) session says.
  const hasExpiringDocument = attachments.documents.some(
    (document) => document.expiresAt !== null,
  );
  const retention: Retention = !AUTH_ENABLED
    ? "none"
    : session.signedIn && !hasExpiringDocument
      ? "kept"
      : "expires";
  const refreshSession = session.refresh;
  useEffect(() => {
    // The page believes "signed in" but the server stored something that
    // expires: find out which of the two is wrong.
    if (AUTH_ENABLED && session.signedIn && hasExpiringDocument) void refreshSession();
  }, [session.signedIn, hasExpiringDocument, refreshSession]);

  /** Confirm the session first, so the notice is right before the file goes;
   * an anonymous answer never blocks the upload. */
  async function handleFileChosen(file: File) {
    await attachments.attachFile(file, refreshSession);
  }
  /** Show a conversation the server gave back. The documents are the ones
   * it says are still attached, so the per-document delete control survives a
   * reload -- see `ConversationView.documents`. */
  function showConversation(
    restoredId: string,
    restoredTurns: ThreadTurn[],
    restoredDocuments: AttachedDocument[],
  ) {
    setConversationId(restoredId);
    setTurns(restoredTurns);
    setIsSessionEnded(false);
    attachments.restore(restoredDocuments);
  }
  useRestoreFromQueryString(setQuestion, setLanguage);
  useRestoreConversation(language, {
    onRestored: showConversation,
    onSessionEnded: () => setIsSessionEnded(true),
    checkSession: refreshSession,
  });
  const ownConversations = useConversationList(session.signedIn, language, conversationId);

  /** Open one of the signed-in person's own conversations from the list. */
  async function handleOpenConversation(id: string) {
    const outcome = await readConversation({ conversationId: id, language });
    if (outcome.kind === "refused") {
      setStatus("error");
      setErrorMessage(outcome.data.detail);
      return;
    }
    showConversation(
      outcome.data.conversation_id,
      outcome.data.turns.map(replayedTurn),
      (outcome.data.documents ?? []).map(attachedFromConversationCard),
    );
    storeConversationId(outcome.data.conversation_id, true);
  }

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
          {AUTH_ENABLED && session.isResolved && (
            <div className="mt-3">
              <AccountBar
                signedIn={session.signedIn}
                email={session.email}
                language={language}
              />
            </div>
          )}
        </div>
      </header>

      <main className={`${COLUMN} flex flex-1 flex-col gap-8 py-6 sm:py-8`}>
        <RedirectNotice notice={redirectNotice} language={language} />
        {AUTH_ENABLED && isSessionEnded && <SessionEndedNotice language={language} />}
        {AUTH_ENABLED && (
          <YourConversations
            conversations={ownConversations}
            currentId={conversationId}
            language={language}
            onOpen={(id) => void handleOpenConversation(id)}
          />
        )}

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
        <StandingHelplines language={language} />

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
          * This list survives a page reload: `GET /conversations/{id}` returns
          * `documents` (metadata only -- ids, filenames, page counts, never
          * content), and `useRestoreConversation` adopts them above. That is
          * what makes per-document deletion reachable after a refresh rather
          * than only within the session that uploaded the file -- an
          * authenticated upload is retained *until the owner deletes it*, and
          * a promise whose only mechanism disappears on reload is not a
          * promise. "Delete this conversation and the documents in it" (below)
          * remains the bulk withdrawal.
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
            retention={retention}
          />
          <DocumentUpload
            language={language}
            conversationId={conversationId}
            retention={retention}
            isUploading={attachments.uploadingFilename !== null}
            uploadingFilename={attachments.uploadingFilename}
            documentCount={attachments.documents.length}
            canOfferSignIn={session.isResolved}
            onFileChosen={(file) => void handleFileChosen(file)}
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
