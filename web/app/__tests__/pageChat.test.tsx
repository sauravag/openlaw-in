import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { askQuestion, readConversation } from "@/lib/api";
import { getStrings } from "@/lib/strings";
import type { AnswerData } from "@/lib/types";

// The chat layout (owner's brief): composer pinned at the bottom, Enter
// sends, focus stays in the composer, the newest answer's START is scrolled
// into view, and helplines sit in a side pane / mobile sheet.
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
    listConversations: vi.fn(async () => ({
      kind: "list" as const,
      data: { conversations: [], next_offset: null },
    })),
    readAuthAgreement: vi.fn(async () => null),
  };
});

const mockedAsk = vi.mocked(askQuestion);
const strings = getStrings("en");

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
  conversation_id: "conv-1",
};

function answer(text: string, extra: Partial<AnswerData> = {}) {
  return { kind: "answer" as const, data: { ...baseAnswer, answer: text, ...extra } };
}

function composer(): HTMLTextAreaElement {
  return screen.getByLabelText(strings.questionLabel) as HTMLTextAreaElement;
}

function threadText(): string {
  return screen.queryByRole("list", { name: strings.threadHeading })?.textContent ?? "";
}

beforeEach(() => {
  mockedAsk.mockReset();
  vi.mocked(readConversation).mockReset();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  mode.enabled = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ signedIn: false, email: null })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.documentElement.lang = "en";
});

describe("the composer", () => {
  it("sends on Enter", async () => {
    mockedAsk.mockResolvedValue(answer("Done."));
    render(<HomePage />);

    await userEvent.type(composer(), "Can my landlord evict me?{Enter}");

    await waitFor(() => expect(mockedAsk).toHaveBeenCalledTimes(1));
    expect(mockedAsk).toHaveBeenCalledWith(
      expect.objectContaining({ question: "Can my landlord evict me?" }),
    );
    expect(await screen.findByText("Done.")).toBeInTheDocument();
  });

  it("inserts a newline on Shift+Enter and does not send", async () => {
    render(<HomePage />);

    await userEvent.type(composer(), "line one{Shift>}{Enter}{/Shift}line two");

    expect(composer()).toHaveValue("line one\nline two");
    expect(mockedAsk).not.toHaveBeenCalled();
  });

  it("does not send while an input method is still composing (Hindi IMEs)", () => {
    render(<HomePage />);
    fireEvent.change(composer(), { target: { value: "बेदखली" } });

    fireEvent.keyDown(composer(), { key: "Enter", isComposing: true });
    fireEvent.keyDown(composer(), { key: "Enter", keyCode: 229 });

    expect(mockedAsk).not.toHaveBeenCalled();
  });

  it("does not send a second time on Enter while the first is in flight", async () => {
    mockedAsk.mockReturnValue(new Promise(() => {}));
    render(<HomePage />);

    await userEvent.type(composer(), "first{Enter}");
    await userEvent.type(composer(), "second{Enter}");

    expect(mockedAsk).toHaveBeenCalledTimes(1);
    // What was typed during the wait is kept for when the reply lands.
    expect(composer()).toHaveValue("second");
  });

  it("shows a pending reply with the question and the localised status while waiting", async () => {
    mockedAsk.mockReturnValue(new Promise(() => {}));
    render(<HomePage />);

    await userEvent.type(composer(), "Is this legal?{Enter}");

    const pending = await screen.findByTestId("pending-reply");
    expect(pending).toHaveTextContent("Is this legal?");
    expect(pending).toHaveTextContent(strings.thinkingStatus);
    expect(composer()).toHaveValue("");
  });

  it("puts the question back in the box when the request itself fails", async () => {
    mockedAsk.mockRejectedValue(new Error("boom"));
    render(<HomePage />);

    await userEvent.type(composer(), "my question{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(/something went wrong/i);
    expect(composer()).toHaveValue("my question");
  });
});

describe("a conversation without reloading (the core regression)", () => {
  it("renders two consecutive questions and both answers, oldest first", async () => {
    mockedAsk
      .mockResolvedValueOnce(answer("First answer."))
      .mockResolvedValueOnce(answer("Second answer."));
    render(<HomePage />);

    await userEvent.type(composer(), "First question{Enter}");
    await screen.findByText("First answer.");
    expect(composer()).toHaveValue("");
    expect(composer()).toHaveFocus();

    await userEvent.type(composer(), "Second question{Enter}");
    await screen.findByText("Second answer.");

    const text = threadText();
    expect(text.indexOf("First question")).toBeLessThan(text.indexOf("First answer."));
    expect(text.indexOf("First answer.")).toBeLessThan(text.indexOf("Second question"));
    expect(text.indexOf("Second question")).toBeLessThan(text.indexOf("Second answer."));
    expect(mockedAsk).toHaveBeenLastCalledWith(
      expect.objectContaining({ question: "Second question", conversationId: "conv-1" }),
    );
  });

  it("returns focus to the composer after an answer sent with the button", async () => {
    mockedAsk.mockResolvedValue(answer("Done."));
    render(<HomePage />);

    await userEvent.type(composer(), "q");
    await userEvent.click(screen.getByRole("button", { name: strings.askButton }));
    await screen.findByText("Done.");

    expect(composer()).toHaveFocus();
  });
});

describe("scrolling and announcing a reply", () => {
  it("scrolls the START of the new answer turn into view, crisis block first", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    mockedAsk.mockResolvedValue(
      answer("Legal analysis.", {
        crisis_response: "If you are in danger right now, call 112.",
        helplines: [{ number: "112", label: "Emergency" }],
      }),
    );
    render(<HomePage />);

    await userEvent.type(composer(), "he is hitting me{Enter}");
    await screen.findByText("Legal analysis.");

    const startCalls = scroll.mock.calls
      .map((call, index) => ({ options: call[0], target: scroll.mock.contexts[index] as Element }))
      .filter(({ options }) => typeof options === "object" && options?.block === "start");
    expect(startCalls.length).toBeGreaterThan(0);
    const target = startCalls[startCalls.length - 1].target;
    expect(target).toHaveTextContent("Legal analysis.");
    // The element scrolled to the top leads with the crisis box: the first
    // thing a person sees is help, not legal analysis.
    const crisis = within(target as HTMLElement).getByTestId("crisis");
    expect((target.textContent ?? "").indexOf("call 112")).toBeLessThan(
      (target.textContent ?? "").indexOf("Legal analysis."),
    );
    expect(target.querySelector("[data-testid]")).toBe(crisis);
  });

  it("scrolls the person's own question into view the moment it is sent", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    mockedAsk.mockReturnValue(new Promise(() => {}));
    render(<HomePage />);

    await userEvent.type(composer(), "Is this legal?{Enter}");
    const pending = await screen.findByTestId("pending-reply");

    const targets = scroll.mock.contexts as Element[];
    expect(targets.some((target) => target === pending || target.contains(pending))).toBe(true);
  });

  it("does not scroll again on unrelated renders, so reading history is not interrupted", async () => {
    mockedAsk.mockResolvedValue(answer("Done."));
    render(<HomePage />);
    await userEvent.type(composer(), "q{Enter}");
    await screen.findByText("Done.");
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");

    await userEvent.type(composer(), "typing a follow-up");
    await userEvent.selectOptions(screen.getByRole("combobox"), "hi");

    expect(scroll).not.toHaveBeenCalled();
  });

  it("announces each reply in a polite live region instead of moving focus", async () => {
    mockedAsk
      .mockResolvedValueOnce(answer("First answer."))
      .mockResolvedValueOnce(answer("Second answer."));
    render(<HomePage />);
    const region = screen.getByTestId("reply-announcer");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toBeEmptyDOMElement();

    await userEvent.type(composer(), "one{Enter}");
    await screen.findByText("First answer.");
    expect(region).toHaveTextContent(strings.replyArrivedAnnouncement(1));
    expect(composer()).toHaveFocus();

    await userEvent.type(composer(), "two{Enter}");
    await screen.findByText("Second answer.");
    expect(region).toHaveTextContent(strings.replyArrivedAnnouncement(2));
  });

  it("announces nothing for a conversation restored after a reload", async () => {
    window.sessionStorage.setItem("openlaw.conversation_id", "conv-9");
    vi.mocked(readConversation).mockResolvedValue({
      kind: "conversation",
      data: {
        conversation_id: "conv-9",
        turns: [
          { turn_index: 0, question: "Old?", answer: "Old answer.", document_answer: null, language: "en" },
        ],
      },
    });
    render(<HomePage />);

    await screen.findByText("Old answer.");
    expect(screen.getByTestId("reply-announcer")).toBeEmptyDOMElement();
  });
});

describe("the side pane", () => {
  it("holds the full static helpline list", () => {
    render(<HomePage />);
    const pane = screen.getByRole("complementary", { name: strings.helpButton });

    for (const number of ["112", "181", "1091", "1098", "14416", "15100"]) {
      expect(pane.querySelector(`a[href="tel:${number}"]`)).not.toBeNull();
    }
    expect(within(pane).getByRole("heading", { name: strings.helplineHeading })).toBeInTheDocument();
  });

  it("fills the composer with an example question and focuses it, without sending", async () => {
    render(<HomePage />);
    const pane = screen.getByRole("complementary", { name: strings.helpButton });
    const [example] = strings.exampleQuestions;

    await userEvent.click(within(pane).getByRole("button", { name: example }));

    expect(composer()).toHaveValue(example);
    expect(composer()).toHaveFocus();
    expect(mockedAsk).not.toHaveBeenCalled();
  });

  it("keeps a one-tap 112 call link in the header", () => {
    render(<HomePage />);
    const header = screen.getByRole("banner");
    expect(within(header).getByRole("link", { name: strings.emergencyCallLabel })).toHaveAttribute(
      "href",
      "tel:112",
    );
  });
});

describe("the mobile help sheet", () => {
  it("opens as a modal dialog with focus inside, and closes on Escape returning focus", async () => {
    render(<HomePage />);
    const trigger = within(screen.getByRole("banner")).getByRole("link", {
      name: strings.helpButton,
    });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(trigger);

    const sheet = screen.getByRole("dialog", { name: strings.helpButton });
    expect(sheet).toHaveAttribute("aria-modal", "true");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(sheet.contains(document.activeElement)).toBe(true);
    expect(sheet.querySelector('a[href="tel:112"]')).not.toBeNull();

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    // Closing hides the sheet; it never removes the helplines from the page.
    expect(screen.getByRole("complementary", { name: strings.helpButton })).toBeInTheDocument();
  });

  it("closes from its close button", async () => {
    render(<HomePage />);
    await userEvent.click(
      within(screen.getByRole("banner")).getByRole("link", { name: strings.helpButton }),
    );

    await userEvent.click(screen.getByRole("button", { name: strings.closeHelpButton }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps Tab inside the open sheet", async () => {
    render(<HomePage />);
    await userEvent.click(
      within(screen.getByRole("banner")).getByRole("link", { name: strings.helpButton }),
    );
    const sheet = screen.getByRole("dialog");

    for (let step = 0; step < 30; step += 1) {
      await userEvent.tab();
      expect(sheet.contains(document.activeElement)).toBe(true);
    }
    await act(async () => {
      await userEvent.tab({ shift: true });
    });
    expect(sheet.contains(document.activeElement)).toBe(true);
  });

  it("closes after an example is picked, leaving the caret in the composer", async () => {
    render(<HomePage />);
    await userEvent.click(
      within(screen.getByRole("banner")).getByRole("link", { name: strings.helpButton }),
    );
    const [, second] = strings.exampleQuestions;

    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: second }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(composer()).toHaveValue(second);
    expect(composer()).toHaveFocus();
  });
});
