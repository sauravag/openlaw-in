import { describe, expect, it } from 'vitest'
import { appendTurn, latestTurn, turnCrisis, type ThreadTurn } from '@/lib/thread'
import type { AnswerData, RefusalData } from '@/lib/types'

const answer: AnswerData = {
  answer: 'Notice must be one month.',
  sources: [],
  language: 'en',
  corpus_version: 'v2026.08',
  footer: 'NALSA: 15100',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
  is_answerable: true,
  quota_remaining: 2,
}

function turn(overrides: Partial<ThreadTurn> = {}): ThreadTurn {
  return {
    id: 't1',
    question: 'How much notice?',
    language: 'en',
    attachedFilenames: [],
    outcome: { kind: 'answer', data: answer },
    ...overrides,
  }
}

describe('appendTurn', () => {
  it('returns a new list with the newest turn last, leaving the old list alone', () => {
    const first = [turn()]
    const both = appendTurn(first, turn({ id: 't2', question: 'And the deposit?' }))

    expect(both.map((item) => item.id)).toEqual(['t1', 't2'])
    expect(first).toHaveLength(1)
    expect(both).not.toBe(first)
  })
})

describe('latestTurn', () => {
  it('is null for an empty thread and the last turn otherwise', () => {
    expect(latestTurn([])).toBeNull()
    expect(latestTurn([turn(), turn({ id: 't2' })])?.id).toBe('t2')
  })
})

describe('turnCrisis', () => {
  it('finds the crisis attached to an answered turn', () => {
    const crisis = turnCrisis(
      turn({
        outcome: {
          kind: 'answer',
          data: { ...answer, crisis_response: 'Call 112 now.', language: 'hi' },
        },
      }),
    )
    expect(crisis).toEqual({
      crisisResponse: 'Call 112 now.',
      helplines: answer.helplines,
      language: 'hi',
    })
  })

  it('finds the crisis attached to a refused turn, using the turn language', () => {
    const refusal: RefusalData = {
      detail: 'Allowance used up.',
      escalation_footer: 'NALSA: 15100',
      crisis_response: 'Call 181 now.',
      helplines: [{ number: '181', label: "Women's helpline" }],
    }
    const crisis = turnCrisis(
      turn({ language: 'hi', outcome: { kind: 'refused', data: refusal } }),
    )
    expect(crisis?.crisisResponse).toBe('Call 181 now.')
    expect(crisis?.language).toBe('hi')
  })

  it('is null when there is no crisis, and for a replayed turn without one', () => {
    expect(turnCrisis(turn())).toBeNull()
    expect(
      turnCrisis(
        turn({
          outcome: {
            kind: 'replay',
            data: {
              turn_index: 0,
              question: 'q',
              answer: 'a',
              document_answer: null,
              language: 'en',
            },
          },
        }),
      ),
    ).toBeNull()
  })

  // HIGH-3: this returned null for every `replay` outcome, so a restored
  // crisis turn could not hoist its own helpline card. Revert the change and
  // both expectations below fail.
  it('reads the crisis off a replayed turn, in that turn own language', () => {
    const crisis = turnCrisis(
      turn({
        language: 'en',
        outcome: {
          kind: 'replay',
          data: {
            turn_index: 0,
            question: 'मैं आत्महत्या करना चाहता हूँ',
            answer: 'मदद अभी उपलब्ध है -- नीचे देखें।',
            document_answer: null,
            language: 'hi',
            crisis_response: 'अगर आप खुद को नुकसान पहुँचाने के बारे में सोच रहे हैं',
            helplines: [{ number: '14416', label: 'टेली-मानस' }],
            is_answerable: true,
          },
        },
      }),
    )

    expect(crisis?.crisisResponse).toContain('नुकसान')
    expect(crisis?.helplines).toEqual([{ number: '14416', label: 'टेली-मानस' }])
    expect(crisis?.language).toBe('hi')
  })

  it('tolerates a replayed turn from a server with no helpline field', () => {
    const crisis = turnCrisis(
      turn({
        outcome: {
          kind: 'replay',
          data: {
            turn_index: 0,
            question: 'q',
            answer: 'a',
            document_answer: null,
            language: 'en',
            crisis_response: 'Help is available.',
          },
        },
      }),
    )

    expect(crisis?.helplines).toEqual([])
  })
})
