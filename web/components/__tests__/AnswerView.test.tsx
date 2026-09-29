import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AnswerView from '../AnswerView'
import type { AnswerData, SourceCard } from '@/lib/types'

/** `SourceCard` gained `kind` and the judgment fields in Plan 6 Task 7; this
 * keeps every fixture below to the one interesting field per test. */
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

const baseAnswer: AnswerData = {
  answer: 'The deposit must be refunded within one month.',
  sources: [card()],
  language: 'en',
  corpus_version: 'v2026.08',
  footer: 'This is legal information, not legal advice. NALSA: 15100',
  crisis_response: null,
  helplines: [{ number: '15100', label: 'Free legal aid (NALSA)' }],
  is_answerable: true,
  quota_remaining: 29,
}

describe('AnswerView', () => {
  it('shows the answer text', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/deposit must be refunded/i)).toBeInTheDocument()
  })

  it('renders every source as a link to the government document', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(
      screen.getByRole('link', { name: /Model Tenancy Act/i }),
    ).toHaveAttribute('href', 'https://www.indiacode.nic.in/handle/123456789/1')
  })

  it('shows the corpus version so the answer is visibly time-bound', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByText(/v2026\.08/)).toBeInTheDocument()
  })

  it('always shows the legal-aid escalation footer', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getAllByText(/15100/).length).toBeGreaterThan(0)
  })

  it('renders the crisis response above the answer', () => {
    const { container } = render(
      <AnswerView
        data={{ ...baseAnswer, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    const text = container.textContent ?? ''
    expect(text.indexOf('Call 181')).toBeLessThan(text.indexOf('deposit must be'))
  })

  it('marks the crisis response as an alert region for screen readers', () => {
    render(
      <AnswerView
        data={{ ...baseAnswer, crisis_response: 'Call 181 for help now.' }}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Call 181')
  })

  it('shows a clear message when no answer could be grounded', () => {
    render(<AnswerView data={{ ...baseAnswer, is_answerable: false, sources: [] }} />)
    expect(screen.getByTestId('unanswerable')).toBeInTheDocument()
  })

  it('omits the sources section entirely when there are no sources', () => {
    render(<AnswerView data={{ ...baseAnswer, sources: [] }} />)
    expect(screen.queryByRole('heading', { name: /where this comes from/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /model tenancy act/i })).not.toBeInTheDocument()
  })

  it('renders tap-to-call links from structured helplines inside the crisis box, not from prose (C2)', () => {
    render(
      <AnswerView
        data={{
          ...baseAnswer,
          crisis_response: 'Call 112 for help now.',
          helplines: [
            { number: '112', label: 'Emergency' },
            { number: '181', label: "Women's helpline" },
          ],
        }}
      />,
    )
    const alert = screen.getByRole('alert')
    expect(alert.querySelector('a[href="tel:112"]')).toHaveTextContent('Emergency: 112')
    expect(alert.querySelector('a[href="tel:181"]')).toHaveTextContent("Women's helpline: 181")
  })

  it('does not duplicate the helpline list in the footer when a crisis box is already showing it', () => {
    render(
      <AnswerView
        data={{ ...baseAnswer, crisis_response: 'Call 112 for help now.' }}
      />,
    )
    expect(screen.getAllByRole('link', { name: /15100/ })).toHaveLength(1)
  })

  it('renders the NALSA helpline as a tap-to-call link in the footer on an ordinary (non-crisis) turn', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByRole('link', { name: /15100/ })).toHaveAttribute('href', 'tel:15100')
  })

  it('does not render a source as a clickable link when its URL is not https (M6)', () => {
    render(
      <AnswerView
        data={{
          ...baseAnswer,
          sources: [
            card({
              chunk_id: 'b',
              source_url: 'http://example.com/insecure',
              title: 'Bad Source',
            }),
          ],
        }}
      />,
    )
    expect(screen.queryByRole('link', { name: /bad source/i })).not.toBeInTheDocument()
    expect(screen.getByText(/bad source/i)).toBeInTheDocument()
  })

  it('renders its own strings in Hindi when the answer language is hi (H3)', () => {
    render(<AnswerView data={{ ...baseAnswer, language: 'hi' }} />)
    expect(screen.getByText('यह जानकारी कहाँ से है')).toBeInTheDocument()
    expect(screen.getByText(/v2026\.08.*स्नैपशॉट/)).toBeInTheDocument()
  })

  it('localises the Sources region aria-label instead of hardcoding English (LOW, re-review)', () => {
    render(<AnswerView data={{ ...baseAnswer, language: 'hi' }} />)
    expect(screen.getByRole('region', { name: 'यह जानकारी कहाँ से है' })).toBeInTheDocument()
  })

  it('marks the cited law with its own heading so it is distinct from the system framing', () => {
    render(<AnswerView data={baseAnswer} />)
    expect(screen.getByRole('heading', { name: /what the law says/i })).toBeInTheDocument()
    expect(
      screen.getByText(/assembled only from the sources listed below/i),
    ).toBeInTheDocument()
  })

  it('does not claim "what the law says" when nothing could be grounded in a source', () => {
    // An unanswerable turn carries no sources, so there is no law to be
    // quoting -- heading it "What the law says" would dress up a
    // "we found nothing" response as a legal statement.
    render(<AnswerView data={{ ...baseAnswer, is_answerable: false, sources: [] }} />)
    expect(
      screen.queryByRole('heading', { name: /what the law says/i }),
    ).not.toBeInTheDocument()
  })

  it('shows the unanswerable framing sentence outside the answer paragraph, in the answer language', () => {
    render(
      <AnswerView
        data={{ ...baseAnswer, is_answerable: false, sources: [], language: 'hi' }}
      />,
    )
    expect(
      screen.getByText('आप फिर भी एक निःशुल्क कानूनी सहायता वकील से मदद पा सकते हैं।'),
    ).toBeInTheDocument()
  })
})
