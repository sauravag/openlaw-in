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
