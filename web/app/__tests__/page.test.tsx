import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "../page";
import { askQuestion } from "@/lib/api";
import type { AnswerData, RefusalData } from "@/lib/types";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, askQuestion: vi.fn() };
});

const mockedAskQuestion = vi.mocked(askQuestion);

describe("HomePage", () => {
  beforeEach(() => {
    mockedAskQuestion.mockReset();
  });

  it("defaults to English and sends the selected language explicitly on submit", async () => {
    const answer: AnswerData = {
      answer: "Notice must be one month.",
      sources: [],
      language: "en",
      corpus_version: "v2026.08",
      footer: "NALSA: 15100",
      crisis_response: null,
      is_answerable: true,
      quota_remaining: 2,
    };
    mockedAskQuestion.mockResolvedValue({ kind: "answer", data: answer });

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

  it("sends Hindi explicitly after the user switches the language selector", async () => {
    const answer: AnswerData = {
      answer: "उत्तर",
      sources: [],
      language: "hi",
      corpus_version: "v2026.08",
      footer: "NALSA: 15100",
      crisis_response: null,
      is_answerable: true,
      quota_remaining: 2,
    };
    mockedAskQuestion.mockResolvedValue({ kind: "answer", data: answer });

    render(<HomePage />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "hi");
    await userEvent.type(screen.getByLabelText(/your question/i), "बेदखली नोटिस?");
    await userEvent.click(screen.getByRole("button", { name: /ask/i }));

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

    resolvePromise!({
      kind: "answer",
      data: {
        answer: "done",
        sources: [],
        language: "en",
        corpus_version: "v2026.08",
        footer: "footer",
        crisis_response: null,
        is_answerable: true,
        quota_remaining: 1,
      },
    });
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
