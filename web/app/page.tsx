"use client";

import { useEffect, useState } from "react";
import AccountBar from "@/components/AccountBar";
import AttachmentStatus, { hasAttachmentActivity } from "@/components/AttachmentStatus";
import AttachmentTray from "@/components/AttachmentTray";
import ChatHeader from "@/components/ChatHeader";
import ChatLayout from "@/components/ChatLayout";
import Composer from "@/components/Composer";
import MessageList from "@/components/MessageList";
import MisconfigurationBanner from "@/components/MisconfigurationBanner";
import RedirectNotice from "@/components/RedirectNotice";
import SessionEndedNotice from "@/components/SessionEndedNotice";
import SidePane from "@/components/SidePane";
import YourConversations from "@/components/YourConversations";
import { AUTH_ENABLED } from "@/lib/authMode";
import { DEFAULT_LANGUAGE, type Language } from "@/lib/languages";
import { useRestoreFromQueryString } from "@/lib/pageEffects";
import { getStrings } from "@/lib/strings";
import { latestTurn } from "@/lib/thread";
import { useAuthMismatch } from "@/lib/useAuthAgreement";
import { useChat, type ChatState } from "@/lib/useChat";
import { useComposer, type ComposerState } from "@/lib/useComposer";
import { useConversationList } from "@/lib/useConversationList";
import { useConversationPointer, type ConversationPointer } from "@/lib/useConversationPointer";
import { useDocuments, type DocumentsState } from "@/lib/useDocuments";
import { useHelpSheet, type HelpSheet } from "@/lib/useHelpSheet";
import { useRedirectNotice, type RedirectNotice as RedirectNoticeKind } from "@/lib/useRedirectNotice";
import { useRestoreConversation } from "@/lib/useRestoreConversation";
import { useRetention } from "@/lib/useRetention";
import { useSession, type SessionHandle } from "@/lib/useSession";
import type { Retention } from "@/lib/documents";
import type { ConversationListItemData } from "@/lib/types";

/** Task 6 review, H3: screen readers must pronounce Hindi with a Hindi voice,
 * not the `lang="en"` the server-rendered shell starts with. */
function useDocumentLanguage(language: Language["code"]): void {
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
}

interface PageNoticesProps {
  language: Language["code"];
  isMisconfigured: boolean;
  redirectNotice: RedirectNoticeKind | null;
  isSessionEnded: boolean;
  onMigrated: () => void;
}

/** Banners above the conversation, each where it was before the chat layout. */
function PageNotices(props: PageNoticesProps) {
  const { language } = props;
  return (
    <>
      {props.isMisconfigured && <MisconfigurationBanner language={language} />}
      <RedirectNotice notice={props.redirectNotice} language={language} onMigrated={props.onMigrated} />
      {AUTH_ENABLED && props.isSessionEnded && <SessionEndedNotice language={language} />}
      <noscript>
        <p className="rounded-xl border border-line bg-tint p-4 text-sm text-ink">
          {getStrings(language).noScriptNotice}
        </p>
      </noscript>
    </>
  );
}

interface AccountSectionProps {
  language: Language["code"];
  session: SessionHandle;
  conversations: readonly ConversationListItemData[];
  currentId: string | null;
  onOpen: (id: string) => void;
}

/** Mode B only, in the side pane. `AUTH_ENABLED` is referenced directly so the
 * Mode A build folds all of it away (`npm run check:mode-a-bundle`). */
function AccountSection(props: AccountSectionProps) {
  if (!AUTH_ENABLED) return null;
  return (
    <>
      <YourConversations
        conversations={props.conversations}
        currentId={props.currentId}
        language={props.language}
        onOpen={props.onOpen}
      />
      {props.session.isResolved && (
        <AccountBar signedIn={props.session.signedIn} email={props.session.email} language={props.language} />
      )}
    </>
  );
}

interface ComposerAreaProps {
  language: Language["code"];
  onLanguageChange: (code: Language["code"]) => void;
  composer: ComposerState;
  chat: ChatState;
  attachments: DocumentsState;
  pointer: ConversationPointer;
  retention: Retention;
  session: SessionHandle;
}

/** The composer with its attachment chips and paperclip tray wired in. */
function ComposerArea({ language, composer, chat, attachments, retention, session, ...rest }: ComposerAreaProps) {
  const isNoticeShownAbove = hasAttachmentActivity(attachments.documents, attachments.uploadingFilename);
  return (
    <Composer
      language={language}
      onLanguageChange={rest.onLanguageChange}
      question={composer.question}
      onQuestionChange={composer.setQuestion}
      onSend={() => void composer.send()}
      isLoading={chat.status === "loading"}
      textareaRef={composer.textareaRef}
      attachmentStatus={
        <AttachmentStatus
          language={language}
          documents={attachments.documents}
          deletingId={attachments.deletingId}
          uploadingFilename={attachments.uploadingFilename}
          retention={retention}
          canOfferSignIn={session.isResolved}
          onDelete={(documentId) => void attachments.removeDocument(documentId)}
        />
      }
      attachControl={
        <AttachmentTray
          language={language}
          conversationId={rest.pointer.conversationId}
          retention={retention}
          canOfferSignIn={session.isResolved}
          documentCount={attachments.documents.length}
          uploadingFilename={attachments.uploadingFilename}
          isNoticeShownAbove={isNoticeShownAbove}
          // Confirm the session first, so the notice is right before the file
          // goes; an anonymous answer never blocks the upload.
          onFileChosen={(file) => void attachments.attachFile(file, session.refresh)}
        />
      }
    />
  );
}

/** Pick an example: close the mobile sheet and put the question in the box. */
function pickExample(help: HelpSheet, composer: ComposerState): (question: string) => void {
  return (question) => {
    composer.fill(question);
    help.close();
  };
}

/**
 * The conversation interface, laid out as a chat (the owner's brief): header
 * at the top, the thread scrolling in the middle, the question box pinned at
 * the bottom, and the helplines in a side pane (a sheet on phones).
 *
 * State lives in hooks -- `useChat` (turns, the in-flight question, errors),
 * `useComposer` (the box and its caret), `useDocuments` (attachments),
 * `useRetention`, `useHelpSheet` -- and layout in components. Every safety
 * property of the single-column page is kept: the static helpline block is in
 * the server HTML in every mode (`SidePane`), each crisis block is the first
 * child of its own reply and is what the reply scroll brings into view
 * (`MessageList`), every refusal carries helplines, and the question form
 * still works without JavaScript (`Composer`).
 *
 * Requirement 6: no authentication is required anywhere on this page; quota is
 * tracked by the cookie `create_app`'s middleware sets on the proxied response.
 */
export default function HomePage() {
  // Must run before `useRestoreFromQueryString`, which may rewrite the URL.
  const redirectNotice = useRedirectNotice();
  const session = useSession();
  const isMisconfigured = useAuthMismatch();
  const [language, setLanguage] = useState<Language["code"]>(DEFAULT_LANGUAGE);
  const pointer = useConversationPointer(session.signedIn);
  const attachments = useDocuments({ language, conversationId: pointer.conversationId, onConversationStarted: pointer.remember });
  const retention = useRetention(attachments.documents, session);
  const chat = useChat({ language, pointer, attachments });
  const composer = useComposer(chat.ask, latestTurn(chat.turns)?.id ?? null);
  const help = useHelpSheet();
  useRestoreFromQueryString(composer.setQuestion, setLanguage);
  useRestoreConversation(language, { onRestored: chat.showConversation, onSessionEnded: chat.markSessionEnded, checkSession: session.refresh });
  const [listRefreshes, setListRefreshes] = useState(0);
  const ownConversations = useConversationList(session.signedIn, language, `${pointer.conversationId ?? ""}:${listRefreshes}`);
  useDocumentLanguage(language);

  return (
    <ChatLayout
      isSheetOpen={help.isOpen}
      onCloseSheet={help.close}
      header={<ChatHeader language={language} isHelpOpen={help.isOpen} onOpenHelp={help.open} />}
      messages={
        <MessageList
          language={language}
          notices={<PageNotices language={language} isMisconfigured={isMisconfigured} redirectNotice={redirectNotice} isSessionEnded={chat.isSessionEnded} onMigrated={() => setListRefreshes((count) => count + 1)} />}
          turns={chat.turns}
          pending={chat.pending}
          status={chat.status}
          errorMessage={chat.errorMessage}
          uploadRefusal={attachments.refusal}
          canDeleteConversation={pointer.conversationId !== null}
          onDeleteConversation={() => void chat.deleteCurrentConversation()}
          onShowExamples={help.open}
        />
      }
      composer={<ComposerArea language={language} onLanguageChange={setLanguage} composer={composer} chat={chat} attachments={attachments} pointer={pointer} retention={retention} session={session} />}
      sidePane={
        <SidePane language={language} isSheetOpen={help.isOpen} onCloseSheet={help.close} onPickExample={pickExample(help, composer)}>
          <AccountSection language={language} session={session} conversations={ownConversations} currentId={pointer.conversationId} onOpen={(id) => void chat.openConversation(id)} />
        </SidePane>
      }
    />
  );
}
