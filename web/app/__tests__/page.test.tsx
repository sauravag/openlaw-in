import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { askQuestion } from "@/lib/api";
import { getFallbackHelplineSet } from "@/lib/fallbackHelplines";
import type { AnswerData, RefusalData } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, askQuestion: vi.fn() };
});

const mockedAskQuestion = vi.mocked(askQuestion);

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

describe("HomePage", () => {
  beforeEach(() => {
    mockedAskQuestion.mockReset();
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    document.documentElement.lang = "en";
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
