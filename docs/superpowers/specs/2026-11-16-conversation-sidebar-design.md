# Conversation sidebar for signed-in users — design

Date: 2026-11-16. Status: description approved by the owner in chat ("Proceed with plan").

## Intent

Signed-in people should be able to see their conversations and move between
them the way they do in ChatGPT or Claude: a list on the left, one click to
switch, a button to start a new chat. Today a signed-in person has only a thin
"Your conversations" list inside the right-hand help pane
(`web/components/YourConversations.tsx`), which reads as an afterthought and
is easy to miss.

The privacy stance that shaped the current design stays: the people this app
serves often use a shared or family phone, so nothing a person asked is shown
in a list unless that person chose to see it on that device.

## Who gets it

- **Signed in (Mode B, session resolved and signed in):** the sidebar.
- **Not signed in, in either mode:** unchanged. One conversation per tab,
  deleted after 24 hours, no list.
- **Mode A build (`OPENLAW_AUTH_ENABLED=false`):** unchanged, and none of the
  sidebar code may reach the bundle. `npm run check:mode-a-bundle` must pass.

The owner tests locally in Mode A. Every rebuild of the live stack stays
Mode A (auth off); the sidebar is verified through tests, not on the live app.

## Goals

1. A left sidebar listing the signed-in person's conversations, newest
   activity first (the existing `GET /conversations` order).
2. Clicking an entry opens that conversation in the chat area without a page
   reload (existing `useChat().openConversation`). The open one is marked.
3. A "New chat" button at the top starts a fresh, empty conversation.
4. Each entry, by default, shows only when it was last active and how many
   documents it holds, e.g. "Today, 9:03 · 2 documents". Nothing the person
   wrote.
5. A "Show titles" switch at the top of the sidebar. When on, each entry also
   shows the start of the first question asked in it.
6. The switch is remembered **per device**, not per account: turning titles
   on at home must not make them appear on a shared phone signed in later.
7. On phones (below `lg`), the list opens as a panel sliding in from the left,
   from a menu button in the header; choosing a chat or "New chat" closes it.
8. English and Hindi for every new piece of text.

## Non-goals

- Any change for people who are not signed in.
- Deleting, renaming, pinning or searching conversations from the list.
  Deleting stays inside the open conversation, where it is today.
- Generating titles with a model. A title is the person's own first question,
  shortened.
- Syncing the "Show titles" choice to the account.

## Anti-goals (a failure even if it works)

- Titles visible by default, or on a device where the person did not turn them
  on.
- Question text sent from the server when titles are off. Hidden-but-present
  content is still on the device and in the network log.
- Any sidebar code, strings or endpoint parameters in the Mode A bundle.
- Losing any existing safety guarantee of the chat page: the phone helpline
  strip stays visible before any tap, the help pane keeps every number, crisis
  blocks still lead their replies.
- One person's list ever including another person's conversation.

## Decisions (the owner's)

- Sidebar for signed-in users only; everyone else keeps today's behaviour.
- Dates and document counts by default; titles only when switched on.
- Live rebuilds stay auth-off.

## Builder's calls (made by Claude, open to change)

- **Titles travel only on request.** `GET /conversations` gains an optional
  `include_titles` query flag (default false). With it, each item carries
  `title`: the first `role="user"` message's content in that conversation,
  whitespace-collapsed and cut to 60 characters at a word boundary with "…",
  or `null` when the conversation has no question yet (started by an upload).
  Without the flag the response is byte-for-byte today's shape.
- **Device memory** for the switch lives in `localStorage`, read and written
  inside try/catch, falling back to "off" when storage is unavailable.
- **The side-pane list goes.** The sidebar replaces `YourConversations` in the
  right-hand pane, so the list exists in one place. The account bar (signed-in
  email, sign out) stays in the right-hand pane.
- **Time label:** "Today, 9:03", "Yesterday, 18:40", otherwise "12 Nov" (with
  the year when it is not this year), in the reader's language and locale,
  from `updated_at`.
- **An untitled entry** with titles on shows "Documents only" (Hindi
  equivalent) when `title` is null.
- **"New chat"** clears the thread, the conversation pointer and its stored id,
  and the attached-documents list on screen (it deletes nothing on the
  server), then puts the cursor in the question box. The next question starts
  a new conversation, as on first load.
- **While a reply is loading,** switching and "New chat" are disabled, so an
  answer can never land in the wrong conversation.
- **Refresh:** the list reloads when a conversation starts and after each
  answered question (activity reorders it), using the existing
  `useConversationList` key mechanism. "Show more" loads the next page via the
  existing `next_offset`.
- **Layout:** on `lg` and up, a fixed-width (about 18rem) left column with its
  own scroll, left of the chat; the right-hand help pane is unchanged. At
  1024px wide, the chat column narrows rather than the pane collapsing.
- **Phone panel:** a modal dialog from the left with a dimmed backdrop,
  reusing `useDialogFocus` (focus trapped, Escape and backdrop close it, focus
  returns to the menu button). The menu button appears only when signed in.
- **Accessibility:** the sidebar is a `<nav>` labelled "Your conversations";
  entries are buttons with at least 44px targets; the open one has
  `aria-current="page"`.

- **Where the titled fetch lives.** The request that sends `include_titles`
  sits in its own module, imported only by the sidebar, which is itself
  rendered only behind `AUTH_ENABLED`. The shared `listConversations` in
  `web/lib/api.ts` is not given the flag. The bundle check's `include_titles`
  sentinel proves the Mode A build never contains it.
- **Refetch rules.** The list reloads (back to page 1, dropping extra pages)
  when a conversation starts, after each answered question, on "New chat",
  and when the titles switch flips. Turning titles off clears titles from
  memory immediately and refetches without the flag, so no question text
  stays on screen or in state.
- **Paging.** The list hook returns its items plus whether more exist and a
  way to load them. If the open conversation is beyond the loaded pages, it
  simply is not marked until it is loaded.
- **Nothing is marked** in the new-chat state (no conversation id yet). There
  is no placeholder row.
- **"First question"** means the `role="user"` message with the lowest
  `turn_index` in that conversation. The 60-character cut counts Unicode code
  points (Python `str` length), so Hindi is cut by characters, not bytes, and
  at a word boundary.
- **Switch memory is per device and per person.** The stored choice is keyed
  by a one-way hash of the signed-in email, never the email itself. On one
  device, one person turning titles on does not turn them on for a different
  person who signs in there later. Two devices never share the choice.
- **Sign-out** clears the list, any titles held in memory, and leaves the
  existing signed-out behaviour for the open conversation unchanged.
- **Phone panels are mutually exclusive.** Opening the conversation panel
  closes the help sheet and vice versa; there is one backdrop at a time.

## Constraints

- Backend changes stay in `src/openlaw/api/conversation_list.py` (owner-only
  by verified token `sub`; Mode B router only, so Mode A still 404s).
- Live-DB tests use `openlaw_test`, never the app database. pytest runs in
  both modes (`OPENLAW_TEST_MODE=a|b`); vitest in both.
- Frontend tests cover: titles off by default and no `include_titles` request
  while off; switch persistence per device and storage failure; entry labels
  in both languages; switching and New chat (including disabled while
  loading); phone panel open/close/focus; the sidebar absent when signed out
  and in Mode A.
- Mode A bundle check gains a sentinel for the sidebar (`include_titles`).

## Left to the builder

Component and hook names and file split, exact styling within the existing
Tailwind tokens, test structure, and the order of work.
