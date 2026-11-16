# Conversation Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ChatGPT-style left sidebar of conversations, with one-click switching, "New chat" and opt-in titles, for signed-in users only (Mode B).

**Architecture:** The backend's `GET /conversations` gains an opt-in `include_titles` flag. A sidebar-only frontend module fetches pages (with titles only when the device/person switch is on), a new hook owns paging and refetch rules, and a presentational `ConversationSidebar` renders as a left column on `lg` and a left slide-in panel on phones. Everything is gated behind the build-time `AUTH_ENABLED` constant so the Mode A bundle folds it away; the old right-pane `YourConversations` list is removed.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Next.js 16 / React / TypeScript / Tailwind, vitest + Testing Library, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-conversation-sidebar-design.md`

## Global Constraints

- Sidebar exists only when `AUTH_ENABLED` and the session is resolved and signed in. Not-signed-in behaviour is unchanged in both modes.
- Mode A bundle must contain none of it: `cd web && npm run check:mode-a-bundle` passes, with `include_titles` added as a sentinel.
- `include_titles` defaults to false; without it the response is exactly today's shape (`id`, `created_at`, `updated_at`, `document_count`).
- Title: first `role="user"` message by lowest `turn_index`, whitespace collapsed, at most 60 Unicode code points, cut at a word boundary with "…"; `null` when there is no question.
- Titles switch: default off; stored in `localStorage` keyed by a SHA-256 hex of the signed-in email (never the email); every storage access in try/catch, failure means off.
- Turning titles off clears titles from state immediately and refetches without the flag.
- While a reply is loading, switching conversations and "New chat" are disabled.
- Every new string exists in English and Hindi in `web/lib/strings.ts`.
- Entries are buttons with at least 44px targets (`min-h-11`); the open one has `aria-current="page"`; the sidebar is a `<nav>` labelled "Your conversations".
- Phone panels (conversations, help) are mutually exclusive; one backdrop at a time.
- pytest runs in both modes: `OPENLAW_TEST_MODE=a .venv/bin/python -m pytest` and `OPENLAW_TEST_MODE=b ...` (plain `pytest` fails here). vitest runs in both: `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=false|true npx vitest run`. Live-DB tests use `openlaw_test` only.
- Live stack rebuilds stay Mode A: web image built from `git archive HEAD` with `--build-arg NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=false`, then `OPENLAW_AUTH_ENABLED=false docker compose up -d --no-build web`.
- Commits use `git commit -F <file>` with the trailer lines `Co-Authored-By: WOZCODE <contact@withwoz.com>` and `Claude-Session: https://claude.ai/code/session_01ThhuRWy7woHssszZyNr8ex`.

## Review Focus

1. A conversation whose first question is long Hindi text: the title must cut by characters at a space, never mid-word or into broken bytes (Task 1 test).
2. A titles fetch that resolves after the switch was turned off: stale titles must never be painted (Task 3 test).
3. Two different people on one device: person B must not inherit person A's "on" (Task 2 test).
4. Clicking a conversation while a reply is streaming: must be impossible, so the answer cannot land in the other thread (Task 4 + Task 5 tests).
5. A failed list request (network down, 401 after a session lapse): the sidebar shows no entries and the chat still works; no thrown error (Task 3 test).

---

### Task 1: Backend — opt-in titles on `GET /conversations`

**Files:**
- Modify: `src/openlaw/api/conversation_list.py`
- Test: `tests/api/test_conversation_list.py`

**Interfaces:**
- Produces: query param `include_titles: bool = False`; `ConversationListItem.title: str | None` present in the JSON **only** when `include_titles=true` (use `response_model_exclude_unset=True` or two item models; the existing metadata-only test must still pass unchanged); `TITLE_MAX_CHARS = 60`; `def shorten_title(question: str) -> str`.

- [ ] **Step 1: Write the failing tests** in `tests/api/test_conversation_list.py`:

```python
def test_titles_are_absent_unless_asked_for(tmp_path: Path) -> None:
    client = _mode_b(tmp_path)
    mine = bearer("user-1", "one@example.com")
    _ask(client, mine)
    body = client.get("/conversations", headers=mine).json()
    assert all("title" not in item for item in body["conversations"])

def test_include_titles_returns_the_first_question(tmp_path: Path) -> None:
    client = _mode_b(tmp_path)
    mine = bearer("user-1", "one@example.com")
    conversation = _ask(client, mine)  # asks "my deposit"
    client.post("/ask", json={"question": "second question", "conversation_id": conversation}, headers=mine)
    item = client.get("/conversations?include_titles=true", headers=mine).json()["conversations"][0]
    assert item["title"] == "my deposit"

def test_an_upload_only_conversation_has_a_null_title(tmp_path: Path) -> None:
    client = _mode_b(tmp_path)
    mine = bearer("user-1", "one@example.com")
    upload(client, headers=mine)
    item = client.get("/conversations?include_titles=true", headers=mine).json()["conversations"][0]
    assert item["title"] is None

def test_include_titles_is_still_404_in_mode_a(tmp_path: Path) -> None:
    assert _mode_a(tmp_path).get("/conversations?include_titles=true", headers=bearer()).status_code == 404

@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("short", "short"),
        ("  many   spaces\nhere  ", "many spaces here"),
        ("word " * 20, ("word " * 12).strip() + "…"),  # 60 chars cut at a space
        ("मेरे मकान मालिक ने बिना नोटिस के मुझे घर से निकाल दिया है अब मैं क्या करूँ कृपया बताएं",
         "मेरे मकान मालिक ने बिना नोटिस के मुझे घर से निकाल दिया है…"),
        ("x" * 80, "x" * 59 + "…"),  # no space to cut at: hard cut, still 60 code points
    ],
)
def test_shorten_title(question: str, expected: str) -> None:
    assert shorten_title(question) == expected
    assert len(shorten_title(question)) <= TITLE_MAX_CHARS
```

(Both multi-word literals were computed from the Step 3 rule and checked.)

- [ ] **Step 2: Run to verify they fail**

Run: `OPENLAW_TEST_MODE=b .venv/bin/python -m pytest tests/api/test_conversation_list.py -q`
Expected: the new tests FAIL (ImportError for `shorten_title` / missing `title`).

- [ ] **Step 3: Implement.** In `conversation_list.py`: add `TITLE_MAX_CHARS = 60` and `shorten_title(question: str) -> str` (collapse whitespace with `" ".join(question.split())`; if `len <= 60` return it; else take the first 59 code points; if the 60th code point is not a space and the slice contains a space, back up to the last space; strip; append "…"). Add `include_titles: bool = Query(default=False)`. When true, fetch first questions for the page's ids in **one** query (no N+1): the `Message` row with `role == "user"` and the minimum `turn_index` per `conversation_id` among the page ids, then `shorten_title` each. Item gets `title=None` when absent. Update the module docstring: the list is metadata-only unless the caller opts into titles.

- [ ] **Step 4: Run both modes**

Run: `OPENLAW_TEST_MODE=b .venv/bin/python -m pytest tests/api/test_conversation_list.py -q && OPENLAW_TEST_MODE=a .venv/bin/python -m pytest tests/api/test_conversation_list.py -q`
Expected: all pass in both.

- [ ] **Step 5: Commit** — `feat: opt-in conversation titles on the signed-in conversation list`

---

### Task 2: Titles switch — per device, per person

**Files:**
- Create: `web/lib/useTitlePreference.ts`
- Test: `web/lib/__tests__/useTitlePreference.test.ts`

**Interfaces:**
- Produces: `useTitlePreference(email: string | null): { showTitles: boolean; setShowTitles: (on: boolean) => void }`; storage key `openlaw.sidebar.titles.<sha256-hex-of-lowercased-email>` storing `"on"`; `showTitles` is `false` until the hash resolves, when `email` is null, or when storage throws.

- [ ] **Step 1: Write the failing tests** (`renderHook`, real `localStorage` from jsdom, `crypto.subtle` from Node):
  - `defaults to off for a person who never turned it on`
  - `remembers on for the same person after a remount` (set true → unmount → render again with same email → `waitFor(showTitles === true)`)
  - `a different person on the same device starts off` (A turns on; render with B's email → stays false)
  - `never stores the email itself` (after turning on, no `localStorage` key or value contains `"one@example.com"`)
  - `falls back to off when storage throws` (`vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })` → false, and `setShowTitles(true)` with throwing `setItem` does not throw and keeps in-memory state true for this render)
  - `is off when signed out` (`email: null`)

- [ ] **Step 2: Run** `cd web && npx vitest run lib/__tests__/useTitlePreference.test.ts` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement** with `crypto.subtle.digest('SHA-256', ...)` → hex; resolve the key in an effect, read with try/catch; ignore a resolution that arrives after `email` changed.

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Commit** — `feat: remember the conversation-titles choice per device and per person`

---

### Task 3: Sidebar data — titled fetch module and paging hook

**Files:**
- Create: `web/lib/sidebarConversations.ts` (fetch + validation; imported only by the sidebar hook)
- Create: `web/lib/useSidebarConversations.ts`
- Modify: `web/lib/types.ts` (add `SidebarConversationData`)
- Modify: `web/scripts/check-mode-a-bundle.mjs` (add sentinel `"include_titles"`)
- Test: `web/lib/__tests__/sidebarConversations.test.ts`, `web/lib/__tests__/useSidebarConversations.test.ts`

**Interfaces:**
- Consumes: `CONVERSATIONS_ENDPOINT` from `web/lib/api.ts`; the existing proxy route forwards the query string unchanged.
- Produces:
  - `interface SidebarConversationData extends ConversationListItemData { title?: string | null }`
  - `fetchSidebarPage(params: { language: string; offset: number; includeTitles: boolean }): Promise<{ kind: 'page'; conversations: SidebarConversationData[]; nextOffset: number | null } | { kind: 'failed' }>` — total, never throws; URL is `${CONVERSATIONS_ENDPOINT}?language=..&offset=N` plus `&include_titles=true` **only** when `includeTitles`; validates the body (a `title` that is neither string nor null fails validation).
  - `useSidebarConversations(params: { enabled: boolean; language: string; showTitles: boolean; refreshKey: string }): { conversations: readonly SidebarConversationData[]; hasMore: boolean; loadMore: () => void }`

- [ ] **Step 1: Write the failing tests.** `fetchSidebarPage` (stub `fetch`): `omits include_titles when titles are off` (assert the requested URL does not contain `include_titles`); `sends include_titles=true when on`; `passes offset`; `returns failed on network error, non-2xx, and malformed body`. `useSidebarConversations` (mock `fetchSidebarPage`): `does not fetch when disabled and returns an empty list`; `refetches page 1 when refreshKey changes and drops loaded pages`; `loadMore appends the next page and hasMore follows nextOffset`; `turning titles off clears titles at once and refetches without them` (first render showTitles true with titled items; rerender false → before the refetch resolves, no item has a `title`; refetch called with `includeTitles: false`); `ignores a response that resolves after showTitles changed` (deferred promise for the titled call resolves last → state has no titles); `a failed fetch shows no entries`.

- [ ] **Step 2: Run** `cd web && npx vitest run lib/__tests__/sidebarConversations.test.ts lib/__tests__/useSidebarConversations.test.ts` — Expected: FAIL.

- [ ] **Step 3: Implement** both modules; inside the hook short-circuit on `!AUTH_ENABLED || !enabled` (reference `AUTH_ENABLED` directly so the build folds it). Add `"include_titles"` to the sentinel list in `check-mode-a-bundle.mjs`.

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Commit** — `feat: sidebar conversation data with paging and opt-in titles`

---

### Task 4: Chat — "New chat" and no switching mid-reply

**Files:**
- Modify: `web/lib/useChat.ts`
- Test: `web/lib/__tests__/useChat.test.ts` (create if absent; follow the existing hook-test style in `web/lib/__tests__`)

**Interfaces:**
- Produces: `ChatState.startNewConversation: () => void` — clears turns, `pointer.setConversationId(null)`, `forgetStoredConversationId()`, `attachments.clear()`, error message and pending; no server call. Both `startNewConversation` and `openConversation` are no-ops while `status === 'loading'`.

- [ ] **Step 1: Write the failing tests:** `startNewConversation clears the thread, pointer, stored id and on-screen documents without deleting anything` (assert no `deleteConversation`/`deleteDocument` call); `startNewConversation does nothing while a reply is loading`; `openConversation does nothing while a reply is loading` (assert `readConversation` not called).

- [ ] **Step 2: Run** `cd web && npx vitest run lib/__tests__/useChat.test.ts` — Expected: FAIL.

- [ ] **Step 3: Implement** in `useChat.ts` beside `deleteCurrentConversation`; expose on `ChatState`.

- [ ] **Step 4: Run** — Expected: PASS.

- [ ] **Step 5: Commit** — `feat: start a new chat, and refuse to switch while a reply is loading`

---

### Task 5: `ConversationSidebar` component, labels and copy

**Files:**
- Create: `web/lib/conversationTime.ts`
- Create: `web/components/ConversationSidebar.tsx`
- Modify: `web/lib/strings.ts` (new keys, both languages)
- Test: `web/lib/__tests__/conversationTime.test.ts`, `web/components/__tests__/ConversationSidebar.test.tsx`

**Interfaces:**
- Consumes: `SidebarConversationData` (Task 3).
- Produces:
  - `formatConversationTime(iso: string, language: string, now: Date): string` → "Today, 9:03" / "Yesterday, 18:40" / "12 Nov" / "12 Nov 2025" (Hindi via `hi-IN` locale and Hindi "आज"/"कल" words from strings); invalid ISO returns the input.
  - New `UIStrings` keys: `conversationsNavLabel` ("Your conversations"), `newChatButton` ("New chat"), `showTitlesLabel` ("Show titles"), `documentsOnlyTitle` ("Documents only"), `showMoreConversations` ("Show more"), `conversationsMenuButton` ("Chats"), `closeConversationsPanel` ("Close"), `todayAt: (time: string) => string`, `yesterdayAt: (time: string) => string`, `conversationDocuments: (count: number) => string` ("2 documents", "1 document", and "" for 0). Hindi equivalents in `HI`.
  - `ConversationSidebar(props: { language: string; conversations: readonly SidebarConversationData[]; hasMore: boolean; onLoadMore: () => void; currentId: string | null; showTitles: boolean; onToggleTitles: (on: boolean) => void; onOpen: (id: string) => void; onNewChat: () => void; isBusy: boolean; now?: Date })`
- Remove `yourConversationsHeading`, `conversationListItem`, `openConversationButton`, `currentConversationLabel` only in Task 6, when their last user goes.

- [ ] **Step 1: Write the failing tests.** `conversationTime`: today, yesterday, this year, other year, Hindi today, invalid input. `ConversationSidebar`: `is a nav labelled Your conversations`; `shows time and document count but no title when titles are off` (even if an item carries a `title`); `shows the title when titles are on, and Documents only for a null title`; `marks the open conversation with aria-current=page and does not offer to open it again`; `clicking an entry calls onOpen with its id`; `New chat calls onNewChat`; `entries and New chat are disabled while busy`; `Show more appears only when hasMore`; `the titles switch is a labelled checkbox that calls onToggleTitles`; `renders Hindi copy for language hi`.

- [ ] **Step 2: Run** `cd web && npx vitest run lib/__tests__/conversationTime.test.ts components/__tests__/ConversationSidebar.test.tsx` — Expected: FAIL.

- [ ] **Step 3: Implement.** Layout inside the component: header row (New chat button + titles checkbox), scrolling `<ol>` of entry buttons (`min-h-11`, full width, two lines: title on top when shown, "time · documents" below), "Show more" button at the end. Use existing Tailwind tokens (`text-ink`, `text-muted`, `border-line`, `bg-brand-soft` for the current entry, `text-brand`).

- [ ] **Step 4: Run** — Expected: PASS.

- [ ] **Step 5: Commit** — `feat: conversation sidebar component with time labels and opt-in titles`

---

### Task 6: Page wiring — left column, phone panel, removal of the old list

**Files:**
- Modify: `web/components/ChatLayout.tsx` (optional `leftPane` and conversations-panel backdrop)
- Modify: `web/components/ChatHeader.tsx` (optional "Chats" menu button, Mode B signed in only)
- Create: `web/lib/usePhonePanels.ts`
- Modify: `web/app/page.tsx`
- Delete: `web/components/YourConversations.tsx`, `web/components/__tests__/YourConversations.test.tsx`, `web/lib/useConversationList.ts` (and its test if present), the four old strings keys
- Test: `web/lib/__tests__/usePhonePanels.test.ts`, `web/components/__tests__/ChatLayout.test.tsx`, `web/components/__tests__/ChatHeader.test.tsx`, a page-level test in `web/app/__tests__/` following existing page tests

**Interfaces:**
- Consumes: `useTitlePreference` (T2), `useSidebarConversations` (T3), `ChatState.startNewConversation` (T4), `ConversationSidebar` (T5), `useDialogFocus(containerRef, isOpen, onClose)`.
- Produces: `usePhonePanels(): { help: HelpSheet; conversations: HelpSheet }` (same `{isOpen, open, close}` shape; opening one closes the other), replacing `useHelpSheet` at the page; `ChatLayout` props `leftPane?: ReactNode`, `isConversationsOpen?: boolean`, `onCloseConversations?: () => void`; `ChatHeader` props `showConversationsButton?: boolean`, `isConversationsOpen?: boolean`, `onOpenConversations?: () => void`.

- [ ] **Step 1: Write the failing tests:** `usePhonePanels`: `opening conversations closes help and vice versa`. `ChatLayout`: `renders the left pane before main when given`; `one backdrop when the conversations panel is open, and a click closes it`. `ChatHeader`: `shows the Chats button only when showConversationsButton`, with `aria-expanded`/`aria-controls`. Page (mock `AUTH_ENABLED: true`, a signed-in session, and the fetch layer): `signed in: the sidebar lists conversations and choosing one opens it`; `New chat clears the thread and focuses the question box`; `the list refetches after an answered question`; `signed out: no sidebar and no Chats button`; `the right pane no longer contains a conversation list but still has the account bar`. With `AUTH_ENABLED: false`: `no sidebar, no Chats button, no list request`.

- [ ] **Step 2: Run** `cd web && npx vitest run lib/__tests__/usePhonePanels.test.ts components/__tests__/ChatLayout.test.tsx components/__tests__/ChatHeader.test.tsx app` — Expected: FAIL.

- [ ] **Step 3: Implement.** `lg` and up: `leftPane` is a `w-72 shrink-0 border-r border-line overflow-y-auto` column left of `<main>`, `hidden lg:flex`. Below `lg`: the same sidebar node rendered as a fixed left panel (`z-50`, `max-w-[85vw]`) when `conversations.isOpen`, with `useDialogFocus`; choosing a chat or New chat calls `conversations.close()`. The refresh key is `${pointer.conversationId ?? ''}:${counter}` (so a newly started conversation refetches, as today); bump the counter after each answered question, after a retried migration (existing `onMigrated`), and on New chat. `showTitles` changes are handled inside `useSidebarConversations` (Task 3), not via the key. Choosing New chat focuses `composer.textareaRef`. Gate every sidebar render and the header button behind `AUTH_ENABLED && session.isResolved && session.signedIn`, referencing `AUTH_ENABLED` directly in `page.tsx`. Delete the old list files and strings.

- [ ] **Step 4: Run the full suites and checks**

Run: `cd web && npx tsc --noEmit && npx eslint . && NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=false npx vitest run && NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=true npx vitest run && NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=false npm run build && npm run check:mode-a-bundle`
Expected: all green; the bundle check reports no sentinel (including `include_titles`).

- [ ] **Step 5: Commit** — `feat: conversation sidebar for signed-in users, replacing the side-pane list`

- [ ] **Step 6: Rebuild the live stack in Mode A and confirm nothing changed for an anonymous visitor.** Build the web image from `git archive HEAD` with `NEXT_PUBLIC_OPENLAW_AUTH_ENABLED=false`, restart `web` with `OPENLAW_AUTH_ENABLED=false`, and in Playwright at 1366×850 and 390×844 confirm: no left sidebar, no "Chats" button, helpline strip and side pane as before, a question still answers. (The backend change needs an api image refresh too; use the code-only overlay if PyPI is still slow.)
