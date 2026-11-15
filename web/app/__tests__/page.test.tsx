import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import {
  askQuestion,
  deleteConversation,
  deleteDocument,
  readConversation,
  uploadDocument,
} from "@/lib/api";
import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
import { CONVERSATION_STORAGE_KEY } from "@/lib/thread";
import { getStrings } from "@/lib/strings";
import { accountDeletedNotice } from "@/lib/accountDeletedStrings";
import { ANONYMOUS_UPLOAD_TTL_HOURS } from "@/lib/documents";
import { SESSION_RECHECK_MIN_INTERVAL_MS, SESSION_REQUEST_TIMEOUT_MS } from "@/lib/useSession";
import type { AnswerData, DocumentSummaryData, RefusalData } from "@/lib/types";

// Mode B (accounts enabled) is the mode these tests were written against; the
// Mode A variants live in `pageModeA.test.tsx`. `mode` lets a test flip it.
const mode = vi.hoisted(() => ({ enabled: true }));
vi.mock("@/lib/authMode", () => ({
  get AUTH_ENABLED() {
    return mode.enabled;
  },
}));

// Every network call this page can make is stubbed -- including the four
// Task 17 added. `importActual` keeps the non-network exports (endpoint
// constants) real, since `DocumentUpload` renders one as a form action.
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
const mockedUploadDocument = vi.mocked(uploadDocument);
const mockedDeleteDocument = vi.mocked(deleteDocument);
const mockedDeleteConversation = vi.mocked(deleteConversation);
const mockedReadConversation = vi.mocked(readConversation);

const leaseSummary: DocumentSummaryData = {
  id: "doc-1",
  filename: "lease.pdf",
  page_count: 4,
  conversation_id: "conv-1",
  expires_at: "2026-10-02T00:00:00Z",
  retention_notice:
    "This file is stored encrypted and will be deleted automatically in 24 hours.",
  quota_remaining: 8,
};

function pdf(name = "lease.pdf"): File {
  return new File(["lease text"], name, { type: "application/pdf" });
}

/** Attach `file` through the real file input and settle the upload. */
async function attach(file: File = pdf()) {
  await userEvent.upload(
    screen.getByLabelText(getStrings("en").attachFileLabel),
    file,
  );
}

/**
 * The turns currently on the page.
 *
 * Scoped to the thread list's own children rather than a document-wide
 * `listitem` query: the standing helpline card is a `<ul>` of its own, and so
 * are an answer's sources, so counting every `listitem` would count those too.
 */
function threadItems(): Element[] {
  const thread = screen.queryByRole("list", {
    name: getStrings("en").threadHeading,
  });
  return thread === null ? [] : Array.from(thread.children);
}

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
};

/** Stub `GET /api/auth/session`; a function body can throw to simulate failure. */
function stubSession(body: unknown | (() => never)) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      if (typeof body === "function") return body();
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
}

describe("HomePage", () => {
  beforeEach(() => {
    mockedAskQuestion.mockReset();
    mockedUploadDocument.mockReset();
    mockedDeleteDocument.mockReset();
    mockedDeleteConversation.mockReset();
    mockedReadConversation.mockReset();
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
    mode.enabled = true;
    stubSession({ signedIn: false, email: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.lang = "en";
  });

  it("renders phone numbers on the very first render, before any question is asked (final review, H6)", () => {
    // With JavaScript disabled, `askQuestion` is never reached and `result`
    // never gets set -- every other helpline on this page lives behind that
    // state. This section must be present unconditionally, in the initial
    // render, or a no-JS visitor in danger sees a form and nothing else.
    mockedAskQuestion.mockClear();

    render(<HomePage />);

    expect(mockedAskQuestion).not.toHaveBeenCalled();
    // Scoped to the static helpline block: the always-visible phone strip
    // carries the same numbers too, so "the" 112 link is no longer unique.
    const standing = screen.getByRole("region", { name: getStrings("en").helplineHeading });
    expect(within(standing).getByRole("link", { name: /112/ })).toHaveAttribute("href", "tel:112");
    expect(within(standing).getByRole("link", { name: /15100/ })).toHaveAttribute("href", "tel:15100");
    const strip = screen.getByRole("region", { name: getStrings("en").helplineStripLabel });
    expect(within(strip).getByRole("link", { name: /112$/ })).toHaveAttribute("href", "tel:112");
  });

  it("welcomes a first-time visitor with example questions, and drops them once there is an answer", async () => {
    mockedAskQuestion.mockResolvedValue({ kind: "answer", data: baseAnswer });

    render(<HomePage />);
    const [firstExample] = getStrings("en").exampleQuestions;
    expect(screen.getByRole("heading", { name: /what this is/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: firstExample })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    await screen.findByText(/Notice must be one month/);
    // The empty state is an empty state -- it must not sit there competing
    // with the answer once one exists.
    expect(
      screen.queryByRole("heading", { name: /what this is/i }),
    ).not.toBeInTheDocument();
  });

  it("puts a tapped example question in the box without spending a free question", async () => {
    render(<HomePage />);
    const [firstExample] = getStrings("en").exampleQuestions;

    await userEvent.click(screen.getByRole("button", { name: firstExample }));

    expect(screen.getByLabelText(/your question/i)).toHaveValue(firstExample);
    expect(mockedAskQuestion).not.toHaveBeenCalled();
  });

  it("keeps the legal-information-not-advice line on the page at all times", () => {
    render(<HomePage />);
    expect(
      screen.getByText("This is legal information, not legal advice."),
    ).toBeInTheDocument();
  });

  it("defaults to English and sends the selected language explicitly on submit", async () => {
    mockedAskQuestion.mockResolvedValue({ kind: "answer", data: baseAnswer });

    render(<HomePage />);
    await userEvent.type(
      screen.getByLabelText(/your question/i),
      "How much eviction notice is required?",
    );
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

    await waitFor(() => expect(mockedAskQuestion).toHaveBeenCalledTimes(1));
    expect(mockedAskQuestion).toHaveBeenCalledWith(
      expect.objectContaining({ language: "en" }),
    );
    expect(await screen.findByText(/Notice must be one month/)).toBeInTheDocument();
  });

  it("sends Hindi explicitly after the user switches the language selector, and updates <html lang> (H3)", async () => {
    const answer: AnswerData = { ...baseAnswer, answer: "उत्तर", language: "hi" };
    mockedAskQuestion.mockResolvedValue({ kind: "answer", data: answer });

    render(<HomePage />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "hi");
    expect(document.documentElement.lang).toBe("hi");

    await userEvent.type(screen.getByLabelText(/आपका सवाल/i), "बेदखली नोटिस?");
    await userEvent.click(screen.getByRole("button", { name: /पूछें/i }));

    await waitFor(() =>
      expect(mockedAskQuestion).toHaveBeenCalledWith(
        expect.objectContaining({ language: "hi" }),
      ),
    );
  });

  it("renders a refusal notice, not an answer, on a quota-exceeded response", async () => {
    const refusal: RefusalData = {
      detail: "Free allowance used for this period.",
      quota_remaining: 0,
      signup_required: true,
      escalation_footer: "Call NALSA free legal aid: 15100.",
      crisis_response: null,
      helplines: [{ number: "15100", label: "Free legal aid (NALSA)" }],
      quota_reset_at: "2026-10-01",
      signup_url: "https://openlaw.in/signup",
    };
    mockedAskQuestion.mockResolvedValue({ kind: "refused", status: 429, data: refusal });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

    expect(await screen.findByTestId("refusal")).toBeInTheDocument();
    expect(screen.queryByTestId("unanswerable")).not.toBeInTheDocument();
  });

  it("renders the crisis box from a 500-shaped refusal, with its tap-to-call helplines (C1)", async () => {
    // The exact regression this fix round exists for: a server outage must
    // not strip the helplines the backend deliberately attached.
    const serverErrorRefusal: RefusalData = {
      detail: "Something went wrong on our end. Please try again.",
      escalation_footer: "Call NALSA free legal aid: 15100.",
      crisis_response: "If you are in danger right now, call 112.",
      helplines: [
        { number: "112", label: "Emergency" },
        { number: "181", label: "Women's helpline" },
        { number: "1091", label: "Police women's helpline" },
        { number: "15100", label: "Free legal aid (NALSA)" },
      ],
    };
    mockedAskQuestion.mockResolvedValue({
      kind: "refused",
      status: 500,
      data: serverErrorRefusal,
    });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "my husband beats me");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("If you are in danger right now, call 112.");
    expect(alert.querySelector('a[href="tel:112"]')).toBeInTheDocument();
  });

  it("shows the static fallback helplines when the server told us nothing at all (re-review HIGH)", async () => {
    // Exactly what askQuestion() itself now returns for a network failure,
    // a timeout, or a proxy/gateway error page with no usable body -- see
    // lib/api.ts's fallbackRefusalData(). Asserted end-to-end at the page so
    // the wiring, not just the function, is proven to reach the DOM.
    const fallback = getFallbackHelplineSet("en");
    mockedAskQuestion.mockResolvedValue({
      kind: "refused",
      status: 0,
      data: {
        detail: "Something went wrong. Please try again.",
        escalation_footer: fallback.escalationFooter,
        crisis_response: fallback.crisisResponse,
        helplines: fallback.helplines,
      },
    });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not reach the server/i);
    expect(alert.querySelector('a[href="tel:112"]')).toHaveTextContent("Emergency: 112");
    expect(alert.querySelector('a[href="tel:15100"]')).toBeInTheDocument();
  });

  it("keeps a previously rendered crisis box on screen when a follow-up request fails (M3)", async () => {
    const crisisRefusal: RefusalData = {
      detail: "Free allowance used for this period.",
      quota_remaining: 0,
      signup_required: true,
      escalation_footer: "Call NALSA free legal aid: 15100.",
      crisis_response: "Call 112 for help now.",
      helplines: [{ number: "112", label: "Emergency" }],
      quota_reset_at: "2026-10-01",
      signup_url: "https://openlaw.in/signup",
    };
    mockedAskQuestion.mockResolvedValueOnce({
      kind: "refused",
      status: 429,
      data: crisisRefusal,
    });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q1");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));
    expect(await screen.findByTestId("crisis")).toHaveTextContent("Call 112");

    mockedAskQuestion.mockRejectedValueOnce(new Error("network down"));
    await userEvent.clear(screen.getByLabelText(/your question/i));
    await userEvent.type(screen.getByLabelText(/your question/i), "q2");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

    await screen.findByText(/something went wrong/i);
    // The crisis box from the first, successful response must still be
    // there -- a failed follow-up must never erase already-delivered help.
    expect(screen.getByTestId("crisis")).toHaveTextContent("Call 112");
  });

  it("restores the question from the URL query string on load (M5 no-JS fallback)", () => {
    window.history.replaceState(null, "", "/?question=my+question&language=hi");

    render(<HomePage />);

    expect(screen.getByLabelText(/आपका सवाल/i)).toHaveValue("my question");
    expect(screen.getByRole("combobox")).toHaveValue("hi");
    // Cleaned up afterwards so a refresh does not keep re-populating the form.
    expect(window.location.search).toBe("");
  });

  it("submits via a real GET request as a no-JS fallback (M5)", () => {
    render(<HomePage />);
    expect(screen.getByRole("textbox", { name: /your question/i }).closest("form")).toHaveAttribute(
      "method",
      "get",
    );
  });

  it("disables the submit button while a request is in flight", async () => {
    let resolvePromise: (value: { kind: "answer"; data: AnswerData }) => void;
    mockedAskQuestion.mockReturnValue(
      new Promise((resolve) => {
        resolvePromise = resolve;
      }),
    );

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    const button = screen.getByRole("button", { name: /ask/i });
    await userEvent.click(button);

    expect(screen.getByRole("button", { name: /asking/i })).toBeDisabled();

    resolvePromise!({ kind: "answer", data: { ...baseAnswer, answer: "done" } });
    await screen.findByText("done");
  });

  it("keeps every turn of the conversation on the page, oldest first", async () => {
    mockedAskQuestion
      .mockResolvedValueOnce({
        kind: "answer",
        data: { ...baseAnswer, answer: "First answer.", conversation_id: "conv-1" },
      })
      .mockResolvedValueOnce({
        kind: "answer",
        data: { ...baseAnswer, answer: "Second answer.", conversation_id: "conv-1" },
      });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "First question");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByText("First answer.");

    await userEvent.type(screen.getByLabelText(/your question/i), "Second question");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByText("Second answer.");

    const thread = threadItems()
      .map((item) => item.textContent ?? "")
      .join("\n");
    expect(thread).toContain("First question");
    expect(thread).toContain("Second question");
    expect(thread.indexOf("First answer.")).toBeLessThan(thread.indexOf("Second answer."));
  });

  it("puts a crisis answer first in its own reply, even at the bottom of a long thread", async () => {
    // The ordering requirement a thread could easily have broken: the newest
    // reply sits LAST in the list, below earlier turns' legal analysis. The
    // single-column page hoisted the crisis box above the whole thread; the
    // chat layout keeps it as the FIRST child of its own reply and scrolls
    // that reply's start to the top of the view (next test), so it is still
    // the first thing on screen, never below legal analysis.
    mockedAskQuestion
      .mockResolvedValueOnce({
        kind: "answer",
        data: { ...baseAnswer, answer: "Ordinary legal analysis.", conversation_id: "c" },
      })
      .mockResolvedValueOnce({
        kind: "answer",
        data: {
          ...baseAnswer,
          answer: "More legal analysis.",
          crisis_response: "If you are in danger right now, call 112.",
          helplines: [{ number: "112", label: "Emergency" }],
          conversation_id: "c",
        },
      });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "First question");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByText("Ordinary legal analysis.");

    await userEvent.type(screen.getByLabelText(/your question/i), "he is hitting me");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByText("More legal analysis.");

    // Exactly one crisis box, in the newest turn, ahead of that turn's law.
    expect(screen.getAllByTestId("crisis")).toHaveLength(1);
    const crisis = screen.getByTestId("crisis");
    const newestTurn = threadItems()[1];
    expect(newestTurn.contains(crisis)).toBe(true);
    const text = newestTurn.textContent ?? "";
    expect(text.indexOf("call 112")).toBeLessThan(text.indexOf("More legal analysis."));
    expect(crisis.querySelector('a[href="tel:112"]')).toBeInTheDocument();
    // The help belongs to the turn that triggered it: the earlier, ordinary
    // turn carries no crisis box and no crisis text of its own.
    const earlierTurn = threadItems()[0];
    expect(earlierTurn.contains(crisis)).toBe(false);
    expect(earlierTurn.textContent ?? "").not.toContain("call 112");
  });

  it("keeps focus in the composer and scrolls the reply that leads with the crisis box into view", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    mockedAskQuestion.mockResolvedValue({
      kind: "answer",
      data: {
        ...baseAnswer,
        crisis_response: "If you are in danger right now, call 112.",
        conversation_id: "c",
      },
    });

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByTestId("crisis");

    // Focus no longer moves to the reply (it stranded the question box far
    // above the viewport); the live region announces it instead.
    expect(screen.getByLabelText(/your question/i)).toHaveFocus();
    const index = scroll.mock.calls.findLastIndex(
      ([options]) => typeof options === "object" && options?.block === "start",
    );
    expect(index).toBeGreaterThanOrEqual(0);
    const target = scroll.mock.contexts[index] as HTMLElement;
    expect(target.contains(screen.getByTestId("crisis"))).toBe(true);
    // The scrolled-to reply leads with the crisis box: the helpline text is
    // the first thing in it, not something below an answer.
    expect((target.textContent ?? "").indexOf("call 112")).toBeLessThan(400);
    scroll.mockRestore();
  });

  it.each([
    ["too large", 413, "That file is too large."],
    ["unsupported type", 422, "We can only read PDF, Word and text files."],
    ["extraction failed", 422, "We could not read any text from that file."],
    ["too many documents", 422, "You have already attached the most documents we can read."],
    ["uploads disabled", 422, "Document uploads are switched off right now."],
  ])(
    "delivers helplines when an upload fails: %s",
    async (_name, status, detail) => {
      mockedUploadDocument.mockResolvedValue({
        kind: "refused",
        status,
        data: {
          detail,
          escalation_footer: "Call NALSA free legal aid: 15100.",
          crisis_response: null,
          helplines: [
            { number: "112", label: "Emergency" },
            { number: "15100", label: "Free legal aid (NALSA)" },
          ],
        },
      });

      render(<HomePage />);
      await attach();

      const refusal = await screen.findByTestId("refusal");
      expect(refusal).toHaveTextContent(detail);
      expect(refusal.querySelector('a[href="tel:15100"]')).toBeInTheDocument();
      expect(refusal.querySelector('a[href="tel:112"]')).toBeInTheDocument();
      // An upload failure is not an answer: nothing is added to the thread.
      expect(threadItems()).toHaveLength(0);
    },
  );

  it("attaches an uploaded document to the next question and shows it in the turn", async () => {
    mockedUploadDocument.mockResolvedValue({ kind: "document", data: leaseSummary });
    mockedAskQuestion.mockResolvedValue({
      kind: "answer",
      data: {
        ...baseAnswer,
        conversation_id: "conv-1",
        document_answer: "Your lease says two months of notice.",
        document_citations: [
          { excerpt_id: "doc:1", document_id: "doc-1", filename: "lease.pdf", page_number: 4 },
        ],
      },
    });

    render(<HomePage />);
    await attach();
    await screen.findByRole("button", {
      name: getStrings("en").deleteDocumentLabel("lease.pdf"),
    });

    await userEvent.type(screen.getByLabelText(/your question/i), "What does my lease say?");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    await waitFor(() =>
      expect(mockedAskQuestion).toHaveBeenCalledWith(
        expect.objectContaining({ conversationId: "conv-1", documentIds: ["doc-1"] }),
      ),
    );
    const documentRegion = await screen.findByRole("region", {
      name: getStrings("en").documentCitationsHeading,
    });
    expect(documentRegion).toHaveTextContent("Your lease says two months of notice.");
    expect(documentRegion.querySelectorAll("a")).toHaveLength(0);
  });

  it("runs a whole Hindi thread with a document attached, with no English anywhere in it", async () => {
    mockedUploadDocument.mockResolvedValue({
      kind: "document",
      data: {
        ...leaseSummary,
        retention_notice:
          "यह फ़ाइल एन्क्रिप्टेड रूप में रखी गई है और 24 घंटों में अपने आप मिटा दी जाएगी।",
      },
    });
    mockedAskQuestion.mockResolvedValue({
      kind: "answer",
      data: {
        ...baseAnswer,
        answer: "नोटिस एक महीने का होना चाहिए।",
        language: "hi",
        conversation_id: "conv-1",
        document_answer: "आपके पट्टे में दो महीने लिखा है।",
        document_citations: [
          { excerpt_id: "doc:1", document_id: "doc-1", filename: "lease.pdf", page_number: 4 },
        ],
      },
    });
    const hi = getStrings("hi");

    render(<HomePage />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "hi");
    await userEvent.upload(screen.getByLabelText(hi.attachFileLabel), pdf());
    await screen.findByRole("button", { name: hi.deleteDocumentLabel("lease.pdf") });

    await userEvent.type(screen.getByLabelText(hi.questionLabel), "कितना नोटिस?");
    await userEvent.click(screen.getByRole("button", { name: hi.askButton }));

    await screen.findByText("नोटिस एक महीने का होना चाहिए।");
    expect(screen.getByText(hi.yourQuestionLabel)).toBeInTheDocument();
    expect(screen.getByText(hi.attachedToThisTurnLabel)).toBeInTheDocument();
    const documentRegion = screen.getByRole("region", {
      name: hi.documentCitationsHeading,
    });
    expect(documentRegion).toHaveTextContent(hi.documentNotLawNote);
    expect(documentRegion).toHaveTextContent("आपके पट्टे में दो महीने लिखा है।");
    expect(mockedUploadDocument).toHaveBeenCalledWith(
      expect.objectContaining({ language: "hi" }),
    );
  });

  it("lets a person delete an uploaded document, which is the only way they can", async () => {
    mockedUploadDocument.mockResolvedValue({ kind: "document", data: leaseSummary });
    mockedDeleteDocument.mockResolvedValue({ kind: "deleted" });
    const strings = getStrings("en");

    render(<HomePage />);
    await attach();
    const deleteButton = await screen.findByRole("button", {
      name: strings.deleteDocumentLabel("lease.pdf"),
    });

    await userEvent.click(deleteButton);

    await waitFor(() =>
      expect(mockedDeleteDocument).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: "doc-1" }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: strings.deleteDocumentLabel("lease.pdf") }),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps the document on screen, with help, when deleting it fails", async () => {
    mockedUploadDocument.mockResolvedValue({ kind: "document", data: leaseSummary });
    mockedDeleteDocument.mockResolvedValue({
      kind: "refused",
      status: 404,
      data: {
        detail: "We could not find that.",
        escalation_footer: "Call NALSA free legal aid: 15100.",
        crisis_response: null,
        helplines: [{ number: "15100", label: "Free legal aid (NALSA)" }],
      },
    });
    const strings = getStrings("en");

    render(<HomePage />);
    await attach();
    await userEvent.click(
      await screen.findByRole("button", {
        name: strings.deleteDocumentLabel("lease.pdf"),
      }),
    );

    const refusal = await screen.findByTestId("refusal");
    expect(refusal).toHaveTextContent("We could not find that.");
    expect(refusal.querySelector('a[href="tel:15100"]')).toBeInTheDocument();
  });

  it("deletes the whole conversation, clearing the thread, the documents and the stored id", async () => {
    mockedAskQuestion.mockResolvedValue({
      kind: "answer",
      data: { ...baseAnswer, conversation_id: "conv-1" },
    });
    mockedDeleteConversation.mockResolvedValue({ kind: "deleted" });
    const strings = getStrings("en");

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByText(/Notice must be one month/);
    expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBe("conv-1");

    await userEvent.click(screen.getByRole("button", { name: strings.deleteConversationLabel }));

    await waitFor(() =>
      expect(mockedDeleteConversation).toHaveBeenCalledWith(
        expect.objectContaining({ conversationId: "conv-1" }),
      ),
    );
    await waitFor(() => expect(threadItems()).toHaveLength(0));
    expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull();
  });

  it("restores the conversation stored in sessionStorage after a reload", async () => {
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, "conv-1");
    mockedReadConversation.mockResolvedValue({
      kind: "conversation",
      data: {
        conversation_id: "conv-1",
        turns: [
          {
            turn_index: 0,
            question: "How much notice?",
            answer: "Notice must be one month.",
            document_answer: null,
            language: "en",
          },
        ],
      },
    });

    render(<HomePage />);

    expect(await screen.findByText("How much notice?")).toBeInTheDocument();
    expect(screen.getByText(getStrings("en").restoredTurnNote)).toBeInTheDocument();
  });

  it("restores attached documents so per-document delete survives a reload", async () => {
    // The whole point of `ConversationView.documents`: an authenticated upload
    // is kept until its owner deletes it, and before this the only control
    // behind that promise vanished on refresh.
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, "conv-1");
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
            expires_at: null,
            retention_notice: "Stored encrypted; stays until you delete it.",
          },
        ],
      },
    });
    mockedDeleteDocument.mockResolvedValue({ kind: "deleted" });
    const strings = getStrings("en");

    render(<HomePage />);

    const deleteButton = await screen.findByRole("button", {
      name: strings.deleteDocumentLabel("lease.pdf"),
    });
    expect(
      screen.getByText("Stored encrypted; stays until you delete it."),
    ).toBeInTheDocument();

    await userEvent.click(deleteButton);

    await waitFor(() =>
      expect(mockedDeleteDocument).toHaveBeenCalledWith(
        expect.objectContaining({ documentId: "doc-1" }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: strings.deleteDocumentLabel("lease.pdf"),
        }),
      ).not.toBeInTheDocument(),
    );
  });

  it("restores the transcript even when the response carries no document list", async () => {
    // Failure direction: a missing `documents` field must cost the document
    // list, never the whole thread.
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, "conv-1");
    mockedReadConversation.mockResolvedValue({
      kind: "conversation",
      data: {
        conversation_id: "conv-1",
        turns: [
          {
            turn_index: 0,
            question: "How much notice?",
            answer: "Notice must be one month.",
            document_answer: null,
            language: "en",
          },
        ],
      },
    });

    render(<HomePage />);

    expect(await screen.findByText("How much notice?")).toBeInTheDocument();
  });

  it("forgets a stored conversation the server will not give back", async () => {
    window.sessionStorage.setItem(CONVERSATION_STORAGE_KEY, "conv-gone");
    mockedReadConversation.mockResolvedValue({
      kind: "refused",
      status: 404,
      data: {
        detail: "We could not find that.",
        escalation_footer: "Call NALSA free legal aid: 15100.",
        crisis_response: null,
        helplines: [{ number: "15100", label: "Free legal aid (NALSA)" }],
      },
    });

    render(<HomePage />);

    await waitFor(() =>
      expect(window.sessionStorage.getItem(CONVERSATION_STORAGE_KEY)).toBeNull(),
    );
    // A stale id is not the reader's problem: the page simply starts fresh.
    expect(threadItems()).toHaveLength(0);
  });

  it("offers the upload form as a real multipart POST, so no-JavaScript loses nothing", () => {
    render(<HomePage />);
    const form = screen
      .getByLabelText(getStrings("en").attachFileLabel)
      .closest("form");
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("enctype", "multipart/form-data");
  });

  it("shows a plain error message, not a stack trace, when the network fails", async () => {
    mockedAskQuestion.mockRejectedValue(new Error("boom"));

    render(<HomePage />);
    await userEvent.type(screen.getByLabelText(/your question/i), "q");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /something went wrong/i,
    );
  });
});

describe("HomePage retention and account state (Mode B)", () => {
  const strings = getStrings("en");
  const anonymousNotice = strings.anonymousUploadNotice(ANONYMOUS_UPLOAD_TTL_HOURS);

  beforeEach(() => {
    mockedAskQuestion.mockReset();
    mockedReadConversation.mockReset();
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/");
    mode.enabled = true;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.lang = "en";
  });

  it("tells an anonymous visitor the 24-hour promise, with the sign-in call to action", async () => {
    stubSession({ signedIn: false, email: null });
    render(<HomePage />);

    expect(screen.getByText(anonymousNotice)).toBeInTheDocument();
    const links = await screen.findAllByRole("link", { name: strings.signInCta });
    expect(links.length).toBeGreaterThan(0);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(strings.keptUploadNotice)).not.toBeInTheDocument();
  });

  it("asks for the session exactly once, uncached", async () => {
    stubSession({ signedIn: false, email: null });
    render(<HomePage />);

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch).toHaveBeenCalledWith(
      "/api/auth/session",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("shows the kept notice, the email and sign-out, and no sign-in call to action, once signed in", async () => {
    stubSession({ signedIn: true, email: "asha@example.com" });
    render(<HomePage />);

    expect(await screen.findByText(strings.keptUploadNotice)).toBeInTheDocument();
    expect(screen.queryByText(anonymousNotice)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: strings.signInCta })).not.toBeInTheDocument();
    expect(screen.getByText(strings.signedInAs("asha@example.com"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: strings.signOutButton })).toBeInTheDocument();
  });

  it("does not flash the signed-in notice while the session is still loading, and offers no sign-in link yet", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<HomePage />);

    // The conservative 24-hour promise stays up; only the links wait.
    expect(screen.getByText(anonymousNotice)).toBeInTheDocument();
    expect(screen.queryByText(strings.keptUploadNotice)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: strings.signInCta })).not.toBeInTheDocument();
  });

  it.each([
    ["the request throws", () => stubSession(() => { throw new Error("offline"); })],
    [
      "the server answers with an error status",
      () =>
        vi.stubGlobal(
          "fetch",
          vi.fn(
            async () =>
              new Response(JSON.stringify({ signedIn: true, email: "x@example.com" }), {
                status: 500,
              }),
          ),
        ),
    ],
    [
      "the body is not the session shape",
      () => stubSession({ signedIn: "yes", email: 7 }),
    ],
    [
      "the body is not JSON",
      () =>
        vi.stubGlobal(
          "fetch",
          vi.fn(async () => new Response("<html>", { status: 200 })),
        ),
    ],
  ])("falls back to the conservative 24-hour promise when %s", async (_name, arrange) => {
    arrange();
    render(<HomePage />);

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    // Let the failed fetch settle before asserting nothing changed.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText(anonymousNotice)).toBeInTheDocument();
    expect(screen.queryByText(strings.keptUploadNotice)).not.toBeInTheDocument();
  });

  it("keeps the static helplines and the hoisted crisis box first while signed in", async () => {
    stubSession({ signedIn: true, email: "asha@example.com" });
    mockedAskQuestion.mockResolvedValue({
      kind: "answer",
      data: {
        ...baseAnswer,
        answer: "Legal analysis here.",
        crisis_response: "If you are in danger right now, call 112.",
        helplines: [{ number: "112", label: "Emergency" }],
        conversation_id: "c",
      },
    });

    const { container } = render(<HomePage />);
    expect(screen.getByRole("link", { name: /15100/ })).toHaveAttribute("href", "tel:15100");
    await screen.findByText(strings.keptUploadNotice);
    await userEvent.type(screen.getByLabelText(/your question/i), "he is hitting me");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await screen.findByText("Legal analysis here.");

    const text = container.textContent ?? "";
    expect(text.indexOf("call 112")).toBeLessThan(text.indexOf("Legal analysis here."));
  });

  describe("a session that goes stale while the page is open", () => {
    const live: { body: unknown } = { body: null };

    function stubLiveSession() {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response(JSON.stringify(live.body), { status: 200 })),
      );
    }

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["Date"] });
      live.body = { signedIn: true, email: "asha@example.com" };
      stubLiveSession();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("flips the banner to expires and the account bar to signed out when a focus re-check says anonymous", async () => {
      render(<HomePage />);
      await screen.findByText(strings.keptUploadNotice);

      live.body = { signedIn: false, email: null };
      vi.advanceTimersByTime(SESSION_RECHECK_MIN_INTERVAL_MS + 1);
      window.dispatchEvent(new Event("focus"));

      expect(await screen.findByText(anonymousNotice)).toBeInTheDocument();
      expect(screen.queryByText(strings.keptUploadNotice)).not.toBeInTheDocument();
      expect(screen.queryByText(strings.signedInAs("asha@example.com"))).not.toBeInTheDocument();
      expect(screen.getAllByRole("link", { name: strings.signInCta }).length).toBeGreaterThan(0);
    });

    it("re-checks when the tab becomes visible again", async () => {
      render(<HomePage />);
      await screen.findByText(strings.keptUploadNotice);

      live.body = { signedIn: false, email: null };
      vi.advanceTimersByTime(SESSION_RECHECK_MIN_INTERVAL_MS + 1);
      document.dispatchEvent(new Event("visibilitychange"));

      expect(await screen.findByText(anonymousNotice)).toBeInTheDocument();
    });

    it("throttles focus and visibility re-checks", async () => {
      render(<HomePage />);
      await screen.findByText(strings.keptUploadNotice);
      expect(fetch).toHaveBeenCalledTimes(1);

      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      expect(fetch).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(SESSION_RECHECK_MIN_INTERVAL_MS + 1);
      window.dispatchEvent(new Event("focus"));
      await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
      window.dispatchEvent(new Event("focus"));
      expect(fetch).toHaveBeenCalledTimes(2);
    });

    it("downgrades to expires when an upload comes back with an expiry, even though the pre-upload refresh still says signed in", async () => {
      // The session stays signed in throughout, so ONLY the ground-truth
      // rule (a document with an expiry beats the session) can produce this.
      mockedUploadDocument.mockResolvedValue({ kind: "document", data: leaseSummary });
      render(<HomePage />);
      await screen.findByText(strings.keptUploadNotice);

      await attach();

      expect(await screen.findByText(anonymousNotice)).toBeInTheDocument();
      expect(screen.queryByText(strings.keptUploadNotice)).not.toBeInTheDocument();
    });

    it("finishes the session check before the upload call is made, and still uploads when the reader turned out anonymous", async () => {
      const events: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          await new Promise((resolve) => setTimeout(resolve, 20));
          events.push("session-resolved");
          return new Response(JSON.stringify(live.body), { status: 200 });
        }),
      );
      mockedUploadDocument.mockImplementation(async () => {
        events.push("upload");
        return { kind: "document", data: leaseSummary };
      });
      render(<HomePage />);
      await screen.findByText(strings.keptUploadNotice);
      events.length = 0;

      live.body = { signedIn: false, email: null };
      await attach();

      await waitFor(() => expect(events).toContain("upload"));
      expect(events).toEqual(["session-resolved", "upload"]);
    });
  });

  describe("a hung session request around an upload", () => {
    const hang = { on: false };

    beforeEach(() => {
      hang.on = false;
      vi.stubGlobal(
        "fetch",
        vi.fn((_url: string, init?: RequestInit) =>
          hang.on
            ? new Promise<Response>(() => {
                void init;
              })
            : Promise.resolve(
                new Response(JSON.stringify({ signedIn: true, email: "a@example.com" }), {
                  status: 200,
                }),
              ),
        ),
      );
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    async function chooseFileWhileHung() {
      mockedUploadDocument.mockResolvedValue({ kind: "document", data: leaseSummary });
      render(<HomePage />);
      await screen.findByText(strings.keptUploadNotice);
      hang.on = true;
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      const input = screen.getByLabelText(strings.attachFileLabel);
      fireEvent.change(input, { target: { files: [pdf()] } });
    }

    it("shows the uploading state at once and blocks a second file while the session check is pending", async () => {
      await chooseFileWhileHung();

      expect(screen.getByText(strings.uploadingStatus("lease.pdf"))).toBeInTheDocument();
      expect(screen.getByLabelText(strings.attachFileLabel)).toBeDisabled();
      expect(mockedUploadDocument).not.toHaveBeenCalled();
    });

    it("uploads anyway once the session request has hung for the timeout", async () => {
      await chooseFileWhileHung();
      expect(mockedUploadDocument).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(SESSION_REQUEST_TIMEOUT_MS + 1);
      });

      expect(mockedUploadDocument).toHaveBeenCalledTimes(1);
    });
  });

  describe("redirect flags", () => {
    it("keeps the URL hash when stripping the flags", async () => {
      stubSession({ signedIn: false, email: null });
      window.history.replaceState(null, "", "/?migration=failed#section");
      render(<HomePage />);

      await within(screen.getByTestId("redirect-notice")).findByText(strings.authMigrationFailed);
      expect(window.location.search).toBe("");
      expect(window.location.hash).toBe("#section");
    });

    it("keeps the URL hash when restoring a question from the query string", async () => {
      stubSession({ signedIn: false, email: null });
      window.history.replaceState(null, "", "/?question=hello#section");
      render(<HomePage />);

      await waitFor(() => expect(window.location.search).toBe(""));
      expect(window.location.hash).toBe("#section");
    });

    function region(): HTMLElement {
      return screen.getByTestId("redirect-notice");
    }

    it("explains a failed migration, in a polite live region, and strips the flag", async () => {
      stubSession({ signedIn: true, email: "a@example.com" });
      window.history.replaceState(null, "", "/?migration=failed");
      render(<HomePage />);

      expect(await within(region()).findByText(strings.authMigrationFailed)).toBeInTheDocument();
      expect(region()).toHaveAttribute("aria-live", "polite");
      expect(window.location.search).toBe("");
    });

    it("renders the Hindi migration copy, with no English", async () => {
      stubSession({ signedIn: true, email: "a@example.com" });
      window.history.replaceState(null, "", "/?migration=failed&language=hi");
      render(<HomePage />);

      const hindi = getStrings("hi");
      expect(await within(region()).findByText(hindi.authMigrationFailed)).toBeInTheDocument();
      expect(region().textContent).not.toContain(strings.authMigrationFailed);
    });

    it.each([
      ["removed", true],
      ["remains", false],
    ])("says what was erased after account deletion (identity=%s)", async (value, removed) => {
      stubSession({ signedIn: false, email: null });
      window.history.replaceState(null, "", `/?account=deleted&identity=${value}`);
      render(<HomePage />);

      expect(
        await within(region()).findByText(accountDeletedNotice("en", removed)),
      ).toBeInTheDocument();
      expect(window.location.search).toBe("");
    });

    it("renders the Hindi deletion notice", async () => {
      stubSession({ signedIn: false, email: null });
      window.history.replaceState(null, "", "/?account=deleted&identity=removed&language=hi");
      render(<HomePage />);

      expect(
        await within(region()).findByText(accountDeletedNotice("hi", true)),
      ).toBeInTheDocument();
    });

    it("renders nothing for an unknown value and never echoes it into the DOM", async () => {
      stubSession({ signedIn: false, email: null });
      window.history.replaceState(
        null,
        "",
        "/?migration=<script>zzz</script>&account=deleted&identity=banana",
      );
      const { container } = render(<HomePage />);

      await waitFor(() => expect(fetch).toHaveBeenCalled());
      expect(region()).toBeEmptyDOMElement();
      expect(container.innerHTML).not.toContain("zzz");
      expect(container.innerHTML).not.toContain("banana");
    });

    it("keeps unrelated query parameters when stripping the flags", async () => {
      stubSession({ signedIn: false, email: null });
      window.history.replaceState(null, "", "/?migration=failed&other=1");
      render(<HomePage />);

      await within(region()).findByText(strings.authMigrationFailed);
      expect(window.location.search).toBe("?other=1");
    });
  });
});
