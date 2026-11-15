import type { FormEvent, KeyboardEvent, ReactNode, RefObject } from 'react'
import LanguageSelector from '@/components/LanguageSelector'
import type { Language } from '@/lib/languages'
import { getStrings } from '@/lib/strings'
import { useAutoGrow } from '@/lib/useAutoGrow'
import { useIsHydrated } from '@/lib/useIsHydrated'

const QUESTION_MAX_LENGTH = 2000
/** About six lines of 17px type at 1.6 line height, plus padding; beyond
 * this the box scrolls inside itself instead of eating the thread. */
const TEXTAREA_MAX_HEIGHT_PX = 190
/** The keyCode browsers report for a keystroke an IME is still handling.
 * Older Safari sets this without setting `isComposing`. */
const IME_PROCESSING_KEY_CODE = 229
export const QUESTION_FORM_ID = 'question-form'
/** Auto-grow cannot measure a placeholder, and on a phone the placeholder
 * wraps to two lines; the empty box is two lines tall there so it is not cut
 * off. Once the reader types, the box follows the text. */
const EMPTY_BOX_HEIGHT = { empty: 'min-h-[4.75rem] sm:min-h-12', typed: 'min-h-12' } as const

interface ComposerProps {
  language: Language['code']
  onLanguageChange: (code: Language['code']) => void
  question: string
  onQuestionChange: (value: string) => void
  onSend: () => void
  isLoading: boolean
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** Attached-document chips and notices, shown above the box. */
  attachmentStatus?: ReactNode
  /** The paperclip control, shown at the left of the box. */
  attachControl?: ReactNode
}

/** True for a plain Enter that should send: not Shift+Enter (a newline), and
 * not the Enter that confirms a word in a Hindi or other input method. */
function isSendKey(event: KeyboardEvent<HTMLTextAreaElement>): boolean {
  if (event.key !== 'Enter' || event.shiftKey) return false
  const native = event.nativeEvent
  return !native.isComposing && native.keyCode !== IME_PROCESSING_KEY_CODE
}

function SendIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-5 w-5" fill="currentColor">
      <path d="M10 3.2 4.3 8.9l1.4 1.4L9 7v9.8h2V7l3.3 3.3 1.4-1.4L10 3.2Z" />
    </svg>
  )
}

/** The language selector (joined to the form by `form=`) and the standing
 * "information, not advice" line, under the box. */
type ComposerFooterProps = Pick<ComposerProps, 'language' | 'onLanguageChange'>

function ComposerFooter({ language, onLanguageChange }: ComposerFooterProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1">
      <LanguageSelector
        value={language}
        onChange={onLanguageChange}
        name="language"
        form={QUESTION_FORM_ID}
        compact
      />
      <p className="text-xs text-muted">{getStrings(language).notLegalAdvice}</p>
    </div>
  )
}

interface QuestionTextareaProps
  extends Pick<ComposerProps, 'language' | 'question' | 'onQuestionChange' | 'textareaRef'> {
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void
}

function QuestionTextarea({
  language,
  question,
  onQuestionChange,
  textareaRef,
  onKeyDown,
}: QuestionTextareaProps) {
  const strings = getStrings(language)
  const height = EMPTY_BOX_HEIGHT[question === '' ? 'empty' : 'typed']
  return (
    <>
      <label htmlFor="question-input" className="sr-only">
        {strings.questionLabel}
      </label>
      <textarea
        id="question-input"
        ref={textareaRef}
        name="question"
        rows={1}
        enterKeyHint="send"
        className={`${height} max-h-[190px] w-full resize-none bg-transparent px-2 py-2.5 text-base leading-relaxed text-ink placeholder:text-muted focus:outline-none focus-visible:outline-none sm:text-lg`}
        value={question}
        onChange={(event) => onQuestionChange(event.target.value)}
        onKeyDown={onKeyDown}
        maxLength={QUESTION_MAX_LENGTH}
        placeholder={strings.questionPlaceholder}
        required
      />
    </>
  )
}

interface QuestionFormProps extends Omit<QuestionTextareaProps, 'onKeyDown'> {
  canSend: boolean
  isLoading: boolean
  onSend: () => void
}

function QuestionForm({ canSend, isLoading, onSend, ...textarea }: QuestionFormProps) {
  const strings = getStrings(textarea.language)
  // The server HTML never disables the button: with JavaScript off it is the
  // only way to submit, and the box always starts empty there.
  const isHydrated = useIsHydrated()

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (canSend) onSend()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!isSendKey(event)) return
    event.preventDefault()
    if (canSend) onSend()
  }

  return (
    <form
      id={QUESTION_FORM_ID}
      onSubmit={handleSubmit}
      method="get"
      noValidate
      className="flex min-w-0 flex-1 items-end gap-1.5"
    >
      <QuestionTextarea {...textarea} onKeyDown={handleKeyDown} />
      <button
        type="submit"
        disabled={isHydrated && !canSend}
        aria-label={isLoading ? strings.askingButton : strings.askButton}
        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-brand text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        <SendIcon />
      </button>
    </form>
  )
}

/**
 * The question box, pinned at the bottom of the chat.
 *
 * Enter sends, Shift+Enter adds a line, and an Enter that an input method is
 * still composing is left alone. The box grows to about six lines, then
 * scrolls inside itself. It stays enabled while an answer is on its way so
 * the next question can be typed; only sending waits.
 *
 * **Works without JavaScript.** It is a real `<form method="get">` (Task 6
 * review, M5): a submit before hydration, or with scripts off, is a browser
 * GET to `?question=...&language=...`, which `useRestoreFromQueryString`
 * puts back in the box on load. The language selector sits in the row
 * below, outside the `<form>` element, and joins it through `form=`.
 */
export default function Composer(props: ComposerProps) {
  const { question, isLoading, textareaRef } = props
  const canSend = !isLoading && question.trim().length > 0
  useAutoGrow(textareaRef, question, TEXTAREA_MAX_HEIGHT_PX)
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
      {props.attachmentStatus}
      <div className="relative flex items-end gap-1.5 rounded-2xl border border-line bg-white p-1.5 shadow-sm focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/25">
        {props.attachControl}
        <QuestionForm
          canSend={canSend}
          isLoading={isLoading}
          language={props.language}
          question={question}
          onQuestionChange={props.onQuestionChange}
          onSend={props.onSend}
          textareaRef={textareaRef}
        />
      </div>
      <ComposerFooter language={props.language} onLanguageChange={props.onLanguageChange} />
    </div>
  )
}
