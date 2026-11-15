import type { Ref } from 'react'
import QuestionBubble from '@/components/QuestionBubble'
import { getStrings } from '@/lib/strings'
import type { PendingTurn } from '@/lib/useChat'

interface PendingReplyProps {
  pending: PendingTurn
  language: string
  ref?: Ref<HTMLDivElement>
}

/**
 * The question just sent, with a typing indicator where its reply will go.
 *
 * The status line is the existing localised "looking through the law" copy
 * in a `role="status"` region, so a screen reader hears that something is
 * happening. The dots only move for readers who have not asked for less
 * motion.
 */
export default function PendingReply({ pending, language, ref }: PendingReplyProps) {
  const strings = getStrings(language)
  return (
    <div ref={ref} data-testid="pending-reply" className="flex scroll-mb-4 flex-col gap-5">
      <QuestionBubble
        question={pending.question}
        attachedFilenames={pending.attachedFilenames}
        strings={strings}
      />
      <div role="status" className="flex items-center gap-3 text-base text-muted">
        <span aria-hidden="true" className="flex gap-1">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="h-2 w-2 rounded-full bg-brand/60 motion-safe:animate-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </span>
        {strings.thinkingStatus}
      </div>
    </div>
  )
}
