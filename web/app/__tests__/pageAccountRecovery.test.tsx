import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { listConversations, readConversation } from "@/lib/api";
import { CONVERSATION_KEPT_STORAGE_KEY, CONVERSATION_STORAGE_KEY } from "@/lib/thread";
import { getStrings } from "@/lib/strings";
import type { RefusalData } from "@/lib/types";

// Final review H1: a lapsed session must not orphan a kept conversation, and a
// signed-in person can always find their conversations again.
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
    listConversations: vi.fn(),
  };
});

const mockedRead = vi.mocked(readConversation);
const mockedList = vi.mocked(listConversations);

const notFound: RefusalData = {
  detail: "We could not find that.",
  escalation_footer: "Call NALSA free legal aid: 15100.",
  crisis_response: null,
  helplines: [{ number: "15100", label: "Free legal aid (NALSA)" }],
};

function stubSession(signedIn: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ signedIn, email: signedIn ? "asha@example.com" : null }),
    ),
  );
}

function storeKept(id: string) {
  window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, id);
  window.sessionStorage.setItem(CONVERSATION_KEPT_STORAGE_KEY, "1");
}

const restoredView = {
  kind: "conversation" as const,
  data: {
    conversation_id: "conv-2",
    turns: [
      { turn_index: 0, question: "Earlier question?", answer: "a", document_answer: null, language: "en" },
    ],
  },
};

beforeEach(() => {
  mockedRead.mockReset();
  mockedList.mockReset().mockResolvedValue({
    kind: "list",
    data: { conversations: [], next_offset: null },
  });
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  mode.enabled = true;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("restoring a kept conversation after the session lapsed (Mode B)", () => {
  it("keeps the stored id and says the session ended, with a sign-in link", async () => {
    storeKept("conv-kept");
    stubSession(false);
    mockedRead.mockResolvedValue({ kind: "refused", status: 404, data: notFound });

    render(<HomePage />);

    const strings = getStrings("en");
    expect(await screen.findByText(strings.sessionEndedNotice)).toBeInTheDocument();
    const link = screen.getAllByRole("link", { name: strings.signInCta }).find((a) =>
      a.closest("[data-testid=session-ended]"),
    );
    expect(link).toHaveAttribute("href", "/signup");
    expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe("conv-kept");
  });

  it("says it in Hindi, with a Hindi sign-in link", async () => {
    storeKept("conv-kept");
    window.history.replaceState(null, "", "/?language=hi");
    stubSession(false);
    mockedRead.mockResolvedValue({ kind: "refused", status: 404, data: notFound });

    render(<HomePage />);

    const notice = await screen.findByTestId("session-ended");
    expect(notice).toHaveTextContent(getStrings("hi").sessionEndedNotice);
    expect(notice.querySelector("a")).toHaveAttribute("href", "/signup?language=hi");
  });

  it("forgets the id on a genuine 404 while signed in", async () => {
    storeKept("conv-gone");
    stubSession(true);
    mockedRead.mockResolvedValue({ kind: "refused", status: 404, data: notFound });

    render(<HomePage />);

    await waitFor(() =>
      expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull(),
    );
    expect(screen.queryByTestId("session-ended")).not.toBeInTheDocument();
  });

  it("keeps the id, silently, when a signed-in read fails for another reason", async () => {
    storeKept("conv-kept");
    stubSession(true);
    mockedRead.mockResolvedValue({ kind: "refused", status: 502, data: notFound });

    render(<HomePage />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe("conv-kept");
    expect(screen.queryByTestId("session-ended")).not.toBeInTheDocument();
  });

  it("forgets an anonymous conversation that expired, as before", async () => {
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, "conv-anon");
    stubSession(false);
    mockedRead.mockResolvedValue({ kind: "refused", status: 404, data: notFound });

    render(<HomePage />);

    await waitFor(() =>
      expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull(),
    );
    expect(screen.queryByTestId("session-ended")).not.toBeInTheDocument();
  });
});

describe("your conversations (Mode B, signed in)", () => {
  it("lists them, and opening one restores it and remembers it as kept", async () => {
    stubSession(true);
    mockedList.mockResolvedValue({
      kind: "list",
      data: {
        conversations: [
          { id: "conv-2", created_at: "2026-11-14T10:00:00+00:00", updated_at: "2026-11-14T10:00:00+00:00", document_count: 1 },
        ],
        next_offset: null,
      },
    });
    mockedRead.mockResolvedValue(restoredView);

    render(<HomePage />);
    const strings = getStrings("en");
    await userEvent.click(await screen.findByRole("button", { name: strings.openConversationButton }));

    expect(await screen.findByText("Earlier question?")).toBeInTheDocument();
    expect(mockedRead).toHaveBeenCalledWith(expect.objectContaining({ conversationId: "conv-2" }));
    expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe("conv-2");
    expect(window.sessionStorage.getItem(CONVERSATION_KEPT_STORAGE_KEY)).toBe("1");
  });

  it("asks for no list while anonymous", async () => {
    stubSession(false);

    render(<HomePage />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockedList).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: getStrings("en").yourConversationsHeading }),
    ).not.toBeInTheDocument();
  });

  it("never asks for a list in Mode A", async () => {
    mode.enabled = false;
    vi.stubGlobal("fetch", vi.fn());

    render(<HomePage />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockedList).not.toHaveBeenCalled();
  });
});
