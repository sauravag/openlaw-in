import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { askQuestion, deleteConversation, deleteDocument, readConversation } from "@/lib/api";
import { forgetStoredConversationId } from "@/lib/thread";
import { useChat } from "@/lib/useChat";
import type { ConversationPointer } from "@/lib/useConversationPointer";
import type { DocumentsState } from "@/lib/useDocuments";

vi.mock("@/lib/api", () => ({
  askQuestion: vi.fn(),
  deleteConversation: vi.fn(),
  deleteDocument: vi.fn(),
  readConversation: vi.fn(),
}));
vi.mock("@/lib/thread", async () => {
  const actual = await vi.importActual<typeof import("@/lib/thread")>("@/lib/thread");
  return { ...actual, forgetStoredConversationId: vi.fn() };
});

function fakePointer(conversationId: string | null = "conv-1") {
  return { conversationId, setConversationId: vi.fn(), remember: vi.fn() } satisfies ConversationPointer;
}

function fakeAttachments() {
  return {
    documents: [],
    restore: vi.fn(),
    clear: vi.fn(),
  } as unknown as DocumentsState & { restore: ReturnType<typeof vi.fn>; clear: ReturnType<typeof vi.fn> };
}

function setup() {
  const pointer = fakePointer();
  const attachments = fakeAttachments();
  const hook = renderHook(() => useChat({ language: "en", pointer, attachments }));
  return { pointer, attachments, ...hook };
}

/** Put the chat into `loading` with a reply that never arrives. */
function startLoadingReply(result: { current: ReturnType<typeof useChat> }) {
  vi.mocked(askQuestion).mockReturnValue(new Promise(() => {}));
  act(() => {
    void result.current.ask("When is notice due?");
  });
  expect(result.current.status).toBe("loading");
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("startNewConversation", () => {
  it("clears the thread, pointer, stored id and on-screen documents without deleting anything", () => {
    const { result, pointer, attachments } = setup();
    act(() => {
      result.current.showConversation("conv-1", [], []);
    });

    act(() => {
      result.current.startNewConversation();
    });

    expect(result.current.turns).toEqual([]);
    expect(result.current.errorMessage).toBeNull();
    expect(result.current.pending).toBeNull();
    expect(pointer.setConversationId).toHaveBeenLastCalledWith(null);
    expect(forgetStoredConversationId).toHaveBeenCalledTimes(1);
    expect(attachments.clear).toHaveBeenCalledTimes(1);
    expect(deleteConversation).not.toHaveBeenCalled();
    expect(deleteDocument).not.toHaveBeenCalled();
  });

  it("does nothing while a reply is loading", () => {
    const { result, pointer, attachments } = setup();
    startLoadingReply(result);
    pointer.setConversationId.mockClear();

    act(() => {
      result.current.startNewConversation();
    });

    expect(pointer.setConversationId).not.toHaveBeenCalled();
    expect(forgetStoredConversationId).not.toHaveBeenCalled();
    expect(attachments.clear).not.toHaveBeenCalled();
    expect(result.current.pending).not.toBeNull();
  });
});

describe("openConversation", () => {
  it("does nothing while a reply is loading", async () => {
    const { result, pointer } = setup();
    startLoadingReply(result);
    pointer.setConversationId.mockClear();

    await act(async () => {
      await result.current.openConversation("conv-2");
    });

    expect(readConversation).not.toHaveBeenCalled();
    expect(pointer.setConversationId).not.toHaveBeenCalled();
  });
});

type ReadOutcome = Awaited<ReturnType<typeof readConversation>>;

/** A `readConversation` whose answer the test releases by hand. */
function deferredRead() {
  let resolve!: (outcome: ReadOutcome) => void;
  const promise = new Promise<ReadOutcome>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function conversationView(id: string, answer: string): ReadOutcome {
  return {
    kind: "conversation",
    data: {
      conversation_id: id,
      turns: [{ turn_index: 0, question: `Question in ${id}?`, answer, document_answer: null, language: "en" }],
    },
  } as ReadOutcome;
}

function shownAnswers(turns: ReturnType<typeof useChat>["turns"]): string {
  return JSON.stringify(turns);
}

describe("openConversation arriving after the person moved on", () => {
  it("drops an open that resolves after a question was sent", async () => {
    const { result, pointer, attachments } = setup();
    const slowA = deferredRead();
    vi.mocked(readConversation).mockReturnValueOnce(slowA.promise);
    vi.mocked(askQuestion).mockResolvedValue({ kind: "refused", status: 503, data: { detail: "x" } } as never);

    let opening!: Promise<void>;
    act(() => {
      opening = result.current.openConversation("conv-A");
    });
    await act(async () => {
      await result.current.ask("A new question");
    });
    await act(async () => {
      slowA.resolve(conversationView("conv-A", "Answer from A."));
      await opening;
    });

    expect(pointer.setConversationId).not.toHaveBeenCalledWith("conv-A");
    expect(attachments.restore).not.toHaveBeenCalled();
    expect(shownAnswers(result.current.turns)).not.toContain("Answer from A.");
    expect(result.current.isOpening).toBe(false);
  });

  it("drops an open that resolves after New chat", async () => {
    const { result, pointer, attachments } = setup();
    const slowA = deferredRead();
    vi.mocked(readConversation).mockReturnValueOnce(slowA.promise);

    let opening!: Promise<void>;
    act(() => {
      opening = result.current.openConversation("conv-A");
    });
    act(() => {
      result.current.startNewConversation();
    });
    await act(async () => {
      slowA.resolve(conversationView("conv-A", "Answer from A."));
      await opening;
    });

    expect(result.current.turns).toEqual([]);
    expect(pointer.setConversationId).toHaveBeenLastCalledWith(null);
    expect(attachments.restore).not.toHaveBeenCalled();
  });

  it("shows the conversation clicked last when an earlier open resolves after it", async () => {
    const { result, pointer } = setup();
    const slowA = deferredRead();
    vi.mocked(readConversation)
      .mockReturnValueOnce(slowA.promise)
      .mockResolvedValueOnce(conversationView("conv-B", "Answer from B."));

    let openingA!: Promise<void>;
    act(() => {
      openingA = result.current.openConversation("conv-A");
    });
    await act(async () => {
      await result.current.openConversation("conv-B");
    });
    await act(async () => {
      slowA.resolve(conversationView("conv-A", "Answer from A."));
      await openingA;
    });

    expect(pointer.setConversationId).toHaveBeenLastCalledWith("conv-B");
    expect(shownAnswers(result.current.turns)).toContain("Answer from B.");
    expect(shownAnswers(result.current.turns)).not.toContain("Answer from A.");
  });

  it("drops a refusal from an open the person moved on from", async () => {
    const { result } = setup();
    const slowA = deferredRead();
    vi.mocked(readConversation).mockReturnValueOnce(slowA.promise);

    let opening!: Promise<void>;
    act(() => {
      opening = result.current.openConversation("conv-A");
    });
    act(() => {
      result.current.startNewConversation();
    });
    await act(async () => {
      slowA.resolve({ kind: "refused", status: 404, data: { detail: "Not found." } } as ReadOutcome);
      await opening;
    });

    expect(result.current.errorMessage).toBeNull();
    expect(result.current.status).toBe("idle");
  });

  it("reports isOpening while an open is in flight", async () => {
    const { result } = setup();
    const slowA = deferredRead();
    vi.mocked(readConversation).mockReturnValueOnce(slowA.promise);

    let opening!: Promise<void>;
    act(() => {
      opening = result.current.openConversation("conv-A");
    });
    expect(result.current.isOpening).toBe(true);

    await act(async () => {
      slowA.resolve(conversationView("conv-A", "Answer from A."));
      await opening;
    });
    expect(result.current.isOpening).toBe(false);
  });
});

describe("showConversation", () => {
  it("clears an error left over from the previous conversation", async () => {
    const { result } = setup();
    vi.mocked(readConversation)
      .mockResolvedValueOnce({ kind: "refused", status: 404, data: { detail: "Not found." } } as ReadOutcome)
      .mockResolvedValueOnce(conversationView("conv-B", "Answer from B."));
    await act(async () => {
      await result.current.openConversation("conv-A");
    });
    expect(result.current.errorMessage).toBe("Not found.");

    await act(async () => {
      await result.current.openConversation("conv-B");
    });

    expect(result.current.errorMessage).toBeNull();
    expect(result.current.status).toBe("idle");
  });
});
