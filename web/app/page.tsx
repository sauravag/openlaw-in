"use client";

import { useEffect, useState } from "react";
import AccountBar from "@/components/AccountBar";
import AttachmentStatus, { hasAttachmentActivity } from "@/components/AttachmentStatus";
import AttachmentTray from "@/components/AttachmentTray";
import ChatHeader from "@/components/ChatHeader";
import ChatLayout from "@/components/ChatLayout";
import HelplineStrip from "@/components/HelplineStrip";
import Composer from "@/components/Composer";
import MessageList from "@/components/MessageList";
import MisconfigurationBanner from "@/components/MisconfigurationBanner";
import RedirectNotice from "@/components/RedirectNotice";
import SessionEndedNotice from "@/components/SessionEndedNotice";
import SidePane from "@/components/SidePane";
import SignedInSidebar from "@/components/SignedInSidebar";
import { AUTH_ENABLED } from "@/lib/authMode";
import { DEFAULT_LANGUAGE, type Language } from "@/lib/languages";
import { useRestoreFromQueryString } from "@/lib/pageEffects";
import { getStrings } from "@/lib/strings";
import { latestTurn } from "@/lib/thread";
import { useAuthMismatch } from "@/lib/useAuthAgreement";
import { useChat, type ChatState } from "@/lib/useChat";
import { useComposer, type ComposerState } from "@/lib/useComposer";
import { useConversationPointer, type ConversationPointer } from "@/lib/useConversationPointer";
import { useDocuments, type DocumentsState } from "@/lib/useDocuments";
import { usePhonePanels, type HelpSheet } from "@/lib/usePhonePanels";
import { useRedirectNotice, type RedirectNotice as RedirectNoticeKind } from "@/lib/useRedirectNotice";
import { useRestoreConversation } from "@/lib/useRestoreConversation";
import { useRetention } from "@/lib/useRetention";
import { useSession, type SessionHandle } from "@/lib/useSession";
import type { Retention } from "@/lib/documents";

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
}

/** Mode B only, in the side pane. `AUTH_ENABLED` is referenced directly so the
 * Mode A build folds all of it away (`npm run check:mode-a-bundle`). The
 * person's conversations live in the left sidebar, not here. */
function AccountSection({ language, session }: AccountSectionProps) {
  if (!AUTH_ENABLED || !session.isResolved) return null;
  return <AccountBar signedIn={session.signedIn} email={session.email} language={language} />;
}

/** The composer with its attachment chips and paperclip tray wired in. */
function ComposerArea({ page }: { page: HomeState }) {
  const { language, attachments, retention, session } = page;
  const isNoticeShownAbove = hasAttachmentActivity(
    attachments.documents,
    attachments.uploadingFilename,
  );
  return (
    <Composer
      language={language}
      onLanguageChange={page.setLanguage}
      question={page.composer.question}
      onQuestionChange={page.composer.setQuestion}
      onSend={() => void page.composer.send()}
      isLoading={page.chat.status === "loading"}
      textareaRef={page.composer.textareaRef}
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
          conversationId={page.pointer.conversationId}
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

interface HomeState {
  language: Language["code"];
  setLanguage: (code: Language["code"]) => void;
  session: SessionHandle;
  isMisconfigured: boolean;
  redirectNotice: RedirectNoticeKind | null;
  pointer: ConversationPointer;
  attachments: DocumentsState;
  retention: Retention;
  chat: ChatState;
  composer: ComposerState;
  help: HelpSheet;
  conversationsPanel: HelpSheet;
  /** Any change reloads the sidebar's list from page 1. */
  sidebarRefreshKey: string;
  refreshSidebar: () => void;
}

/**
 * The sidebar's refresh key: the current conversation id (so a newly started
 * conversation shows up) plus a counter bumped after each answered question,
 * a retried migration and New chat.
 */
function useSidebarRefresh(conversationId: string | null) {
  const [refreshes, setRefreshes] = useState(0);
  return {
    sidebarRefreshKey: `${conversationId ?? ""}:${refreshes}`,
    refreshSidebar: () => setRefreshes((count) => count + 1),
  };
}

/** Every piece of the page's state, wired together in dependency order. */
function useHomeState(): HomeState {
  // Must run before `useRestoreFromQueryString`, which may rewrite the URL.
  const redirectNotice = useRedirectNotice();
  const session = useSession();
  const isMisconfigured = useAuthMismatch();
  const [language, setLanguage] = useState<Language["code"]>(DEFAULT_LANGUAGE);
  const pointer = useConversationPointer(session.signedIn);
  const attachments = useDocuments({
    language,
    conversationId: pointer.conversationId,
    onConversationStarted: pointer.remember,
  });
  const retention = useRetention(attachments.documents, session);
  const chat = useChat({ language, pointer, attachments });
  const { sidebarRefreshKey, refreshSidebar } = useSidebarRefresh(pointer.conversationId);
  // An answered question moves its conversation to the top of the list.
  async function askAndRefresh(question: string) {
    const result = await chat.ask(question);
    if (result === "sent") refreshSidebar();
    return result;
  }
  const composer = useComposer(askAndRefresh, latestTurn(chat.turns)?.id ?? null);
  const { help, conversations: conversationsPanel } = usePhonePanels();
  useRestoreFromQueryString(composer.setQuestion, setLanguage);
  useRestoreConversation(language, {
    onRestored: chat.showConversation,
    onSessionEnded: chat.markSessionEnded,
    checkSession: session.refresh,
  });
  useDocumentLanguage(language);
  return {
    language, setLanguage, session, isMisconfigured, redirectNotice, pointer, attachments,
    retention, chat, composer, help, conversationsPanel, sidebarRefreshKey, refreshSidebar,
  };
}

function HomeMessages({ page }: { page: HomeState }) {
  const { language, chat } = page;
  return (
    <MessageList
      language={language}
      notices={
        <PageNotices
          language={language}
          isMisconfigured={page.isMisconfigured}
          redirectNotice={page.redirectNotice}
          isSessionEnded={chat.isSessionEnded}
          onMigrated={page.refreshSidebar}
        />
      }
      turns={chat.turns}
      pending={chat.pending}
      status={chat.status}
      errorMessage={chat.errorMessage}
      uploadRefusal={page.attachments.refusal}
      canDeleteConversation={page.pointer.conversationId !== null}
      onDeleteConversation={() => void chat.deleteCurrentConversation()}
      onShowExamples={page.help.open}
    />
  );
}

function HomeSidePane({ page }: { page: HomeState }) {
  const { language, help, composer } = page;
  // Both actions close the mobile sheet, so what they lead to is not left
  // behind the backdrop (review M6). Desktop ignores the sheet state.
  function pickExample(question: string): void {
    composer.fill(question);
    help.close();
  }
  return (
    <SidePane
      language={language}
      isSheetOpen={help.isOpen}
      onCloseSheet={help.close}
      onPickExample={pickExample}
    >
      <AccountSection language={language} session={page.session} />
    </SidePane>
  );
}

/** Mode B only. The early return on `AUTH_ENABLED` itself (as in
 * `AccountSection`) is what lets the Mode A build drop the sidebar module. */
function HomeSidebar({ page }: { page: HomeState }) {
  if (!AUTH_ENABLED) return null;
  const { chat, composer, conversationsPanel } = page;
  // Both actions close the phone panel, so what they lead to is in view.
  function openConversation(id: string): void {
    conversationsPanel.close();
    void chat.openConversation(id);
  }
  function startNewChat(): void {
    chat.startNewConversation();
    page.refreshSidebar();
    // Focus first: the panel's focus handling then leaves the caret here.
    composer.textareaRef.current?.focus();
    conversationsPanel.close();
  }
  return (
    <SignedInSidebar
      language={page.language}
      email={page.session.email}
      refreshKey={page.sidebarRefreshKey}
      currentId={page.pointer.conversationId}
      isBusy={chat.status === "loading"}
      isPanelOpen={conversationsPanel.isOpen}
      onClosePanel={conversationsPanel.close}
      onOpen={openConversation}
      onNewChat={startNewChat}
    />
  );
}

/**
 * The conversation interface, laid out as a chat (the owner's brief): header
 * at the top, the thread scrolling in the middle, the question box pinned at
 * the bottom, and the helplines in a side pane (a sheet on phones).
 *
 * State lives in hooks -- `useChat` (turns, the in-flight question, errors),
 * `useComposer` (the box and its caret), `useDocuments` (attachments),
 * `useRetention`, `usePhonePanels` -- and layout in components. Every safety
 * property of the single-column page is kept: the static helpline block is in
 * the server HTML in every mode (`SidePane`), every number is visible on a
 * phone before any tap (`HelplineStrip`), each crisis block is the first
 * child of its own reply and is what the reply scroll brings into view
 * (`MessageList`), every refusal carries helplines, and the question form
 * still works without JavaScript (`Composer`).
 *
 * Requirement 6: no authentication is required anywhere on this page; quota is
 * tracked by the cookie `create_app`'s middleware sets on the proxied response.
 */
export default function HomePage() {
  const page = useHomeState();
  const { language, help, conversationsPanel } = page;
  // Mode B, signed in only. With `HomeSidebar`'s own early return, this keeps
  // the sidebar and the Chats button out of the Mode A build.
  const hasSidebar = AUTH_ENABLED && page.session.isResolved && page.session.signedIn;
  return (
    <ChatLayout
      isSheetOpen={help.isOpen}
      onCloseSheet={help.close}
      isConversationsOpen={hasSidebar && conversationsPanel.isOpen}
      onCloseConversations={conversationsPanel.close}
      header={
        <ChatHeader
          language={language}
          isHelpOpen={help.isOpen}
          onOpenHelp={help.open}
          showConversationsButton={hasSidebar}
          isConversationsOpen={conversationsPanel.isOpen}
          onOpenConversations={conversationsPanel.open}
        />
      }
      leftPane={hasSidebar ? <HomeSidebar page={page} /> : null}
      helplineStrip={<HelplineStrip language={language} />}
      messages={<HomeMessages page={page} />}
      composer={<ComposerArea page={page} />}
      sidePane={<HomeSidePane page={page} />}
    />
  );
}
