import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { askQuestion, readConversation } from "@/lib/api";
import { ANONYMOUS_UPLOAD_TTL_HOURS } from "@/lib/documents";
import { CONVERSATION_STORAGE_KEY } from "@/lib/thread";
import { getStrings } from "@/lib/strings";
import { accountDeletedNotice } from "@/lib/accountDeletedStrings";
import type { AnswerData } from "@/lib/types";

// Mode A: no accounts. The build-time flag is off, so the page must carry no
// account affordance, make no session request, and promise no expiry.
vi.mock("@/lib/authMode", () => ({ AUTH_ENABLED: false }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    askQuestion: vi.fn(),
    uploadDocument: vi.fn(),
    deleteDocument: vi.fn(),
    deleteConversation: vi.fn(),
    readConversation: vi.fn(),
    listConversations: vi.fn(async () => ({
      kind: "list" as const,
      data: { conversations: [], next_offset: null },
    })),
    readAuthAgreement: vi.fn(async () => null),
  };
});

const mockedAskQuestion = vi.mocked(askQuestion);
const mockedReadConversation = vi.mocked(readConversation);
const strings = getStrings("en");

const baseAnswer: AnswerData = {
  answer: "Legal analysis here.",
  sources: [],
  language: "en",
  corpus_version: "v2026.08",
  footer: "NALSA: 15100",
  crisis_response: null,
  helplines: [{ number: "15100", label: "Free legal aid (NALSA)" }],
  is_answerable: true,
  quota_remaining: 2,
};

describe("HomePage in Mode A", () => {
  beforeEach(() => {
    mockedAskQuestion.mockReset();
    mockedReadConversation.mockReset();
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.lang = "en";
  });

  it("renders no retention notice and no sign-in affordance anywhere", () => {
    const { container } = render(<HomePage />);

    expect(
      screen.queryByText(strings.anonymousUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(strings.keptUploadNotice)).not.toBeInTheDocument();
    expect(screen.queryByText(strings.signInCta)).not.toBeInTheDocument();
    expect(container.querySelector('a[href*="signup"], a[href*="/account"]')).toBeNull();
    expect(container.querySelector('form[action*="/api/auth"]')).toBeNull();
    expect(screen.queryByTestId("redirect-notice")).not.toBeInTheDocument();
  });

  it("makes no session request", async () => {
    render(<HomePage />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetch).not.toHaveBeenCalled();
  });

  it("makes no session request on focus or visibility change either", async () => {
    render(<HomePage />);
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows the server's expiry on an attached document, and in the upload notice (final review H3b)", async () => {
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, "conv-1");
    const serverSentence = "This file is stored encrypted and will be deleted automatically in 24 hours.";
    mockedReadConversation.mockResolvedValue({
      kind: "conversation",
      data: {
        conversation_id: "conv-1",
        turns: [],
        documents: [
          {
            id: "doc-1",
            filename: "lease.pdf",
            page_count: 3,
            expires_at: "2026-10-02T00:00:00Z",
            retention_notice: serverSentence,
          },
        ],
      },
    });

    render(<HomePage />);

    expect(await screen.findByText("lease.pdf")).toBeInTheDocument();
    // A document that will be deleted is never shown without its notice,
    // even when the frontend was built without accounts.
    expect(screen.getByText(serverSentence)).toBeInTheDocument();
    expect(
      screen.getByText(strings.expiringUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS)),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: strings.signInCta })).not.toBeInTheDocument();
  });

  it.each([
    "/?migration=failed",
    "/?account=deleted&identity=removed",
    "/?account=deleted&identity=remains",
  ])("renders none of the redirect notices for %s, there being no account", async (url) => {
    window.history.replaceState(null, "", url);
    const { container } = render(<HomePage />);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(container.textContent).not.toContain(strings.authMigrationFailed);
    expect(container.textContent).not.toContain(accountDeletedNotice("en", true));
    expect(container.textContent).not.toContain(accountDeletedNotice("en", false));
  });

  it("keeps the static helplines on the first render and the crisis box first in DOM order", async () => {
    mockedAskQuestion.mockResolvedValue({
      kind: "answer",
      data: {
        ...baseAnswer,
        crisis_response: "If you are in danger right now, call 112.",
        helplines: [{ number: "112", label: "Emergency" }],
        conversation_id: "c",
      },
    });

    const { container } = render(<HomePage />);
    // Scoped to the static block: the phone helpline strip repeats 15100.
    const standing = screen.getByRole("region", { name: strings.helplineHeading });
    expect(within(standing).getByRole("link", { name: /15100/ })).toHaveAttribute("href", "tel:15100");
    await userEvent.type(screen.getByLabelText(/your question/i), "he is hitting me");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await waitFor(() => expect(screen.getByTestId("crisis")).toBeInTheDocument());
    await screen.findByText("Legal analysis here.");

    const text = container.textContent ?? "";
    expect(text.indexOf("call 112")).toBeLessThan(text.indexOf("Legal analysis here."));
  });
});
