import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ConversationThread from '../ConversationThread'
import { getStrings } from '@/lib/strings'
import type { ThreadTurn } from '@/lib/thread'
import type { AnswerData, SourceCard } from '@/lib/types'

function card(overrides: Partial<SourceCard> = {}): SourceCard {
  return {
    chunk_id: 'a',
    source_url: 'https://www.indiacode.nic.in/handle/123456789/1',
    kind: 'provision',
    title: 'Model Tenancy Act',
    case_id: null,
    court_code: null,
    decision_date: null,
    judges: [],
    headnote: null,
    ...overrides,
  }
}

const answer: AnswerData = {
  answer: 'Notice must be one month.',
  sources: [card()],
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

describe('ConversationThread', () => {
  it('renders nothing before the first question', () => {
    const { container } = render(<ConversationThread turns={[]} language="en" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('is an ordered list with one item per turn, oldest first', () => {
    render(
      <ConversationThread
        turns={[
          turn({ question: 'First question' }),
          turn({ id: 't2', question: 'Second question' }),
        ]}
        language="en"
      />,
    )
    // Scoped to the thread's own direct children: a turn legitimately contains
    // further lists (sources, helplines), so a document-wide `listitem` query
    // would count those too.
    const thread = screen.getByRole('list', { name: getStrings('en').threadHeading })
    expect(thread.tagName).toBe('OL')
    const items = Array.from(thread.children)
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('First question')
    expect(items[1]).toHaveTextContent('Second question')
  })

  it('shows the person their own question back, labelled as theirs', () => {
    render(<ConversationThread turns={[turn()]} language="en" />)
    expect(screen.getByText(getStrings('en').yourQuestionLabel)).toBeInTheDocument()
    expect(screen.getByText('How much notice?')).toBeInTheDocument()
  })

  it('names the documents that were read for a turn', () => {
    render(
      <ConversationThread
        turns={[turn({ attachedFilenames: ['lease.pdf'] })]}
        language="en"
      />,
    )
    expect(screen.getByText(getStrings('en').attachedToThisTurnLabel)).toBeInTheDocument()
    expect(screen.getByText('lease.pdf')).toBeInTheDocument()
  })

  it('keeps every corpus source a working government link inside the thread', () => {
    render(<ConversationThread turns={[turn()]} language="en" />)
    expect(screen.getByRole('link', { name: /Model Tenancy Act/ })).toHaveAttribute(
      'href',
      'https://www.indiacode.nic.in/handle/123456789/1',
    )
  })

  it('renders a turn that was refused through the refusal surface, help included', () => {
    render(
      <ConversationThread
        turns={[
          turn({
            outcome: {
              kind: 'refused',
              data: {
                detail: 'Free allowance used for this period.',
                escalation_footer: 'Call NALSA free legal aid: 15100.',
                crisis_response: null,
                helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
              },
            },
          }),
        ]}
        language="en"
      />,
    )
    expect(screen.getByTestId('refusal')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /15100/ })).toHaveAttribute('href', 'tel:15100')
  })

  it('omits the crisis box for the turn whose crisis the page has hoisted above the thread', () => {
    const crisisTurn = turn({
      outcome: { kind: 'answer', data: { ...answer, crisis_response: 'Call 112 now.' } },
    })
    render(
      <ConversationThread
        turns={[crisisTurn]}
        language="en"
        suppressCrisisForTurnId="t1"
      />,
    )
    expect(screen.queryByTestId('crisis')).not.toBeInTheDocument()
  })

  it('keeps an older turn crisis box in place, since help once given is never withdrawn', () => {
    const older = turn({
      id: 't1',
      outcome: { kind: 'answer', data: { ...answer, crisis_response: 'Call 112 now.' } },
    })
    render(
      <ConversationThread
        turns={[older, turn({ id: 't2' })]}
        language="en"
        suppressCrisisForTurnId="t2"
      />,
    )
    expect(screen.getByTestId('crisis')).toHaveTextContent('Call 112 now.')
  })

  it('replays a restored turn without pretending its sources are still on the page', () => {
    render(
      <ConversationThread
        turns={[
          turn({
            outcome: {
              kind: 'replay',
              data: {
                turn_index: 0,
                question: 'How much notice?',
                answer: 'Notice must be one month.',
                document_answer: null,
                language: 'en',
              },
            },
          }),
        ]}
        language="en"
      />,
    )
    expect(screen.getByText(getStrings('en').restoredTurnNote)).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: getStrings('en').sourcesHeading }),
    ).not.toBeInTheDocument()
  })

  it('renders a restored turn document answer in the document section, not as law', () => {
    render(
      <ConversationThread
        turns={[
          turn({
            outcome: {
              kind: 'replay',
              data: {
                turn_index: 0,
                question: 'q',
                answer: 'Notice must be one month.',
                document_answer: 'Your lease says two months.',
                language: 'en',
              },
            },
          }),
        ]}
        language="en"
      />,
    )
    const region = screen.getByRole('region', {
      name: getStrings('en').documentCitationsHeading,
    })
    expect(region).toHaveTextContent('Your lease says two months.')
    expect(region).not.toHaveTextContent('Notice must be one month.')
  })

  // HIGH-3: `ReplayedTurn` rendered `<p>{data.answer}</p>` and nothing else,
  // so the single most safety-critical response this product produces was the
  // one that did not survive a reload. Remove the `CrisisAlert` block and this
  // fails.
  it('renders a restored crisis turn helpline card above its answer', () => {
    render(
      <ConversationThread
        turns={[
          turn({
            outcome: {
              kind: 'replay',
              data: {
                turn_index: 0,
                question: 'i want to kill myself',
                answer: 'Help is available right now -- see below.',
                document_answer: null,
                language: 'en',
                crisis_response: 'If you are thinking about harming yourself',
                helplines: [{ number: '14416', label: 'Tele-MANAS' }],
                is_answerable: true,
              },
            },
          }),
        ]}
        language="en"
      />,
    )
    const alert = screen.getByTestId('crisis')
    expect(alert).toHaveTextContent('If you are thinking about harming yourself')
    expect(alert).toHaveTextContent('14416')
  })

  it('omits a restored crisis box the page has already hoisted', () => {
    render(
      <ConversationThread
        turns={[
          turn({
            id: 't1',
            outcome: {
              kind: 'replay',
              data: {
                turn_index: 0,
                question: 'i want to kill myself',
                answer: 'Help is available right now -- see below.',
                document_answer: null,
                language: 'en',
                crisis_response: 'If you are thinking about harming yourself',
                helplines: [{ number: '14416', label: 'Tele-MANAS' }],
                is_answerable: true,
              },
            },
          }),
        ]}
        language="en"
        suppressCrisisForTurnId="t1"
      />,
    )
    expect(screen.queryByTestId('crisis')).not.toBeInTheDocument()
  })

  // MEDIUM-2: `citations={[]}` was hard-coded, so a restored statement about
  // the reader own paperwork outlived the only pointer letting her check it.
  it('renders a restored document claim citation', () => {
    render(
      <ConversationThread
        turns={[
          turn({
            outcome: {
              kind: 'replay',
              data: {
                turn_index: 0,
                question: 'q',
                answer: 'Notice must be one month.',
                document_answer: 'Your lease says two months.',
                language: 'en',
                document_citations: [
                  {
                    excerpt_id: 'doc:d1:p2:0',
                    document_id: 'd1',
                    filename: 'lease.pdf',
                    page_number: 2,
                  },
                ],
              },
            },
          }),
        ]}
        language="en"
      />,
    )
    const region = screen.getByRole('region', {
      name: getStrings('en').documentCitationsHeading,
    })
    expect(region).toHaveTextContent('lease.pdf')
    expect(region).toHaveTextContent('2')
  })
})
