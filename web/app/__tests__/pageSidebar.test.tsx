import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { askQuestion, readConversation } from "@/lib/api";
import { fetchSidebarPage } from "@/lib/sidebarConversations";
import { SIDE_PANE_ID } from "@/components/SidePane";
import { CONVERSATION_STORAGE_KEY } from "@/lib/thread";
import { getStrings } from "@/lib/strings";
import { getSidebarStrings } from "@/lib/sidebarStrings";
import type { AnswerData, SidebarConversationData } from "@/lib/types";

// The conversation sidebar on the page (Mode B, signed in). The sidebar's own
// fetch module is the network layer stubbed here.
const mode = vi.hoisted(() => ({ enabled: true }));
vi.mock("@/lib/authMode", () => ({
  get AUTH_ENABLED() {
    return mode.enabled;
  },
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    askQuestion: vi.fn(),
    uploadDocument: vi.fn(),
    deleteDocument: vi.fn(),
    deleteConversation: vi.fn(),
    readConversation: vi.fn(),
    readAuthAgreement: vi.fn(async () => null),
  };
});

vi.mock("@/lib/sidebarConversations", () => ({ fetchSidebarPage: vi.fn() }));

const mockedAsk = vi.mocked(askQuestion);
const mockedRead = vi.mocked(readConversation);
const mockedPage = vi.mocked(fetchSidebarPage);
const strings = getStrings("en");
const sidebarStrings = getSidebarStrings("en");

const entry: SidebarConversationData = {
  id: "conv-2",
  created_at: "2026-11-14T10:00:00+00:00",
  updated_at: "2026-11-14T10:00:00+00:00",
  document_count: 1,
};

const restoredView = {
  kind: "conversation" as const,
  data: {
    conversation_id: "conv-2",
    turns: [
      { turn_index: 0, question: "Earlier question?", answer: "Earlier answer.", document_answer: null, language: "en" },
    ],
  },
};

const baseAnswer: AnswerData = {
  answer: "Notice must be one month.",
  sources: [],
  language: "en",
  corpus_version: "v2026.08",
  footer: "NALSA: 15100",
  crisis_response: null,
  helplines: [{ number: "15100", label: "Free legal aid (NALSA)" }],
  is_answerable: true,
  quota_remaining: 2,
  conversation_id: "conv-2",
};

function stubSession(signedIn: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ signedIn, email: signedIn ? "asha@example.com" : null })),
  );
}

function sidebar(): HTMLElement {
  return screen.getByRole("navigation", { name: sidebarStrings.conversationsNavLabel });
}

function composer(): HTMLTextAreaElement {
  return screen.getByLabelText(strings.questionLabel) as HTMLTextAreaElement;
}

/** The sidebar entry for `conv-2`: the one button in its list. */
async function findEntry(): Promise<HTMLElement> {
  const nav = await screen.findByRole("navigation", { name: sidebarStrings.conversationsNavLabel });
  const list = await within(nav).findByRole("list");
  return within(list).findByRole("button");
}

/** Let the session request and any effects it triggers settle. */
async function settle(): Promise<void> {
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  mockedAsk.mockReset();
  mockedRead.mockReset();
  mockedPage.mockReset().mockResolvedValue({ kind: "page", conversations: [entry], nextOffset: null });
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  mode.enabled = true;
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.lang = "en";
});

describe("the conversation sidebar (Mode B)", () => {
  it("signed in: the sidebar lists conversations and choosing one opens it", async () => {
    stubSession(true);
    mockedRead.mockResolvedValue(restoredView);
    render(<HomePage />);

    await userEvent.click(await findEntry());

    expect(await screen.findByText("Earlier answer.")).toBeInTheDocument();
    expect(mockedRead).toHaveBeenCalledWith(expect.objectContaining({ conversationId: "conv-2" }));
    expect(mockedPage).toHaveBeenCalledWith(expect.objectContaining({ includeTitles: false }));
    // The open conversation is marked rather than offered again.
    await waitFor(() =>
      expect(within(sidebar()).getByRole("list").querySelector("[aria-current=page]")).not.toBeNull(),
    );
  });

  it("on a phone, the Chats button opens the panel and choosing a chat closes it", async () => {
    stubSession(true);
    mockedRead.mockResolvedValue(restoredView);
    render(<HomePage />);
    const banner = screen.getByRole("banner");

    await userEvent.click(await within(banner).findByRole("button", { name: sidebarStrings.conversationsMenuButton }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("navigation", { name: sidebarStrings.conversationsNavLabel })).toBeInTheDocument();

    await userEvent.click(await findEntry());

    expect(await screen.findByText("Earlier answer.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opening the help sheet closes the conversations panel", async () => {
    stubSession(true);
    render(<HomePage />);
    const banner = screen.getByRole("banner");
    await userEvent.click(await within(banner).findByRole("button", { name: sidebarStrings.conversationsMenuButton }));
    expect(screen.getByRole("dialog")).toHaveAttribute("id", "conversations-panel");

    await userEvent.keyboard("{Escape}");
    await userEvent.click(within(banner).getByRole("link", { name: strings.helpButton }));

    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0]).toHaveAttribute("id", SIDE_PANE_ID);
  });

  it("New chat clears the thread and focuses the question box", async () => {
    stubSession(true);
    mockedRead.mockResolvedValue(restoredView);
    render(<HomePage />);
    await userEvent.click(await findEntry());
    await screen.findByText("Earlier answer.");
    await userEvent.click(screen.getByRole("button", { name: sidebarStrings.conversationsMenuButton }));

    await userEvent.click(within(sidebar()).getByRole("button", { name: sidebarStrings.newChatButton }));

    expect(screen.queryByText("Earlier answer.")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(composer());
  });

  it("the list refetches after an answered question", async () => {
    stubSession(true);
    mockedRead.mockResolvedValue(restoredView);
    mockedAsk.mockResolvedValue({ kind: "answer", data: baseAnswer });
    render(<HomePage />);
    // Open conv-2 first, so the answer below does not change the pointer:
    // only the answered question itself can trigger the refetch.
    await userEvent.click(await findEntry());
    await screen.findByText("Earlier answer.");
    await waitFor(() => expect(mockedPage).toHaveBeenCalled());
    const before = mockedPage.mock.calls.length;

    await userEvent.type(composer(), "And the deposit?{Enter}");

    expect(await screen.findByText("Notice must be one month.")).toBeInTheDocument();
    await waitFor(() => expect(mockedPage.mock.calls.length).toBeGreaterThan(before));
  });

  it("entries are disabled while a conversation is being opened", async () => {
    stubSession(true);
    const other = { ...entry, id: "conv-3" };
    mockedPage.mockResolvedValue({ kind: "page", conversations: [entry, other], nextOffset: null });
    let releaseRead!: (outcome: Awaited<ReturnType<typeof readConversation>>) => void;
    mockedRead.mockReturnValue(new Promise((resolve) => (releaseRead = resolve)));
    render(<HomePage />);
    const nav = await screen.findByRole("navigation", { name: sidebarStrings.conversationsNavLabel });
    const [first, second] = await within(within(nav).getByRole("list")).findAllByRole("button");

    await userEvent.click(first);

    await waitFor(() => expect(second).toBeDisabled());
    releaseRead(restoredView);
    expect(await screen.findByText("Earlier answer.")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(within(nav).getByRole("list")).getByRole("button")).not.toBeDisabled(),
    );
  });

  it("signed out: no sidebar and no Chats button", async () => {
    stubSession(false);
    render(<HomePage />);
    await settle();

    expect(screen.queryByRole("navigation", { name: sidebarStrings.conversationsNavLabel })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: sidebarStrings.conversationsMenuButton })).not.toBeInTheDocument();
    expect(mockedPage).not.toHaveBeenCalled();
  });

  it("the right pane no longer contains a conversation list but still has the account bar", async () => {
    stubSession(true);
    render(<HomePage />);
    await findEntry();

    const pane = document.getElementById(SIDE_PANE_ID) as HTMLElement;
    expect(within(pane).queryByRole("navigation", { name: sidebarStrings.conversationsNavLabel })).not.toBeInTheDocument();
    expect(within(pane).queryByRole("button", { name: sidebarStrings.newChatButton })).not.toBeInTheDocument();
    expect(await within(pane).findByRole("button", { name: strings.signOutButton })).toBeInTheDocument();
  });
});

describe("the conversation sidebar in Mode A", () => {
  it("no sidebar, no Chats button, no list request", async () => {
    mode.enabled = false;
    vi.stubGlobal("fetch", vi.fn());
    render(<HomePage />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.queryByRole("navigation", { name: sidebarStrings.conversationsNavLabel })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: sidebarStrings.conversationsMenuButton })).not.toBeInTheDocument();
    expect(mockedPage).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
