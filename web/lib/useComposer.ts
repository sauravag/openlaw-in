import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import type { AskResult } from './useChat'

export interface ComposerState {
  question: string
  setQuestion: (value: string) => void
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** Put text in the box and hand the reader the caret -- never send it. */
  fill: (text: string) => void
  send: () => Promise<void>
}

/** Below Tailwind's `lg` breakpoint, or any touch-first device: where a soft
 * keyboard is likely, and the side pane is a sheet. */
const PHONE_LIKE_QUERY = '(max-width: 1023.98px), (pointer: coarse)'

/** True on a phone-like device. False where `matchMedia` is unavailable
 * (the server render, jsdom without a stub): the desktop behaviour. */
export function isPhoneLike(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(PHONE_LIKE_QUERY).matches
}

/** True when nothing has focus, or focus was left on the composer's own
 * (now disabled) send button: the reader has not gone anywhere else. */
function isFocusUnclaimed(textarea: HTMLTextAreaElement): boolean {
  const active = document.activeElement
  if (active === null || active === document.body) return true
  return textarea.form?.contains(active) ?? false
}

/**
 * The question box: its text, its caret, and sending from it.
 *
 * Sending clears the box at once (the question moves into the thread as a
 * pending bubble) and keeps the caret in it, so the next question can be
 * typed immediately -- the owner's "I have to refresh" report was the box
 * ending up far above the viewport after an answer. If the call itself fails,
 * the question is put back unless the reader has already typed something new.
 *
 * `replyKey` changes when a reply lands (or a conversation is restored). On a
 * desktop the caret returns to the box then, but only if focus is unclaimed:
 * someone who moved to a helpline or a source meanwhile is not pulled away.
 * On a phone the box is blurred instead, closing the soft keyboard so the
 * reply's start -- crisis box first -- is what is on screen.
 */
export function useComposer(
  ask: (question: string) => Promise<AskResult>,
  replyKey: string | null,
): ComposerState {
  const [question, setQuestion] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const focus = useCallback(() => textareaRef.current?.focus(), [])

  function fill(text: string): void {
    setQuestion(text)
    focus()
  }

  async function send(): Promise<void> {
    const asked = question
    if (asked.trim() === '') return
    setQuestion('')
    focus()
    const result = await ask(asked)
    if (result !== 'sent') setQuestion((current) => (current === '' ? asked : current))
  }

  useEffect(() => {
    const textarea = textareaRef.current
    if (replyKey === null || textarea === null) return
    if (isPhoneLike()) {
      // Close the soft keyboard: left open, the browser keeps the textarea in
      // view by panning, which can push the reply's crisis box (scrolled to
      // the top of the list) off screen. Also covers a restore on load,
      // which must not open the keyboard unasked (review I1, M7).
      if (document.activeElement === textarea) textarea.blur()
      return
    }
    if (isFocusUnclaimed(textarea)) textarea.focus()
  }, [replyKey])

  return { question, setQuestion, textareaRef, fill, send }
}
